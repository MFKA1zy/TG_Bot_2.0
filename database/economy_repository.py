from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from database.reviews_repository import ReviewsRepository, now


class EconomyError(ValueError):
    pass


@dataclass
class ViewReward:
    added: bool
    amount: int
    balance: int
    legacy: bool = False


class EconomyRepository(ReviewsRepository):
    """Shares transaction/connection helpers; users and views remain the same tables."""

    @staticmethod
    async def require_user(db, user_id, activity=False):
        user = await ReviewsRepository.one(db, "SELECT * FROM users WHERE telegram_id=?", (user_id,))
        if not user:
            raise EconomyError("missing")
        if user["is_blocked"] or activity and (not user["captcha_passed"] or not user["language"]):
            raise EconomyError("access")
        return user

    @staticmethod
    async def require_admin(db, actor):
        user = await EconomyRepository.require_user(db, actor)
        if not user["is_admin"]:
            raise EconomyError("access")

    async def settings(self) -> dict:
        async with self.connection() as db:
            return await self._settings(db)

    @staticmethod
    async def _settings(db):
        async with db.execute("SELECT key,value FROM economy_settings") as cursor:
            return {"view_reward": 10, "leaderboard": "earned", **{row[0]: json.loads(row[1]) for row in await cursor.fetchall()}}

    async def configure(self, actor: int, key: str, value):
        if key == "view_reward":
            if type(value) is not int or not 0 <= value <= 1000000:
                raise EconomyError("invalid")
        elif key != "leaderboard" or value not in {"earned", "balance"}:
            raise EconomyError("invalid")
        async with self.connection(True) as db:
            await self.require_admin(db, actor)
            await db.execute("INSERT INTO economy_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,json.dumps(value)))
            await db.execute("INSERT INTO economy_audit(actor_id,action,payload,created_at) VALUES (?,?,?,?)", (actor,"settings",json.dumps({key:value}),now()))

    @staticmethod
    async def _post(db, user_id, operation, amount, reason, key, actor=None, series_id=None, title=None):
        if type(amount) is not int or abs(amount) > 1000000000 or not key or not 1 <= len(reason) <= 500:
            raise EconomyError("invalid")
        if operation in {"view","roulette","admin_credit"} and amount<0 or operation=="admin_debit" and amount>=0:
            raise EconomyError("invalid")
        previous = await ReviewsRepository.one(db, "SELECT * FROM points_ledger WHERE idempotency_key=?", (key,))
        if previous:
            if (previous["user_id"], previous["operation"], previous["amount"], previous["actor_id"], previous["series_id"],previous["reason"]) != (user_id,operation,amount,actor,series_id,reason):
                raise EconomyError("stale")
            return previous
        user = await EconomyRepository.require_user(db, user_id)
        balance = user["points_balance"] + amount
        earned = user["points_earned"] + max(amount,0)
        spent = user["points_spent"] + max(-amount,0)
        if balance < 0:
            raise EconomyError("insufficient")
        if max(balance,earned,spent) > 9000000000000000:
            raise EconomyError("invalid")
        await db.execute("UPDATE users SET points_balance=?,points_earned=?,points_spent=? WHERE telegram_id=?", (balance,earned,spent,user_id))
        cursor = await db.execute("""INSERT INTO points_ledger(user_id,operation,amount,reason,series_id,series_title,actor_id,created_at,idempotency_key,balance_after)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (user_id,operation,amount,reason,series_id,title,actor,now(),key,balance))
        result = await ReviewsRepository.one(db,"SELECT * FROM points_ledger WHERE id=?",(cursor.lastrowid,))
        await cursor.close()
        return result

    async def confirm_view(self, user_id: int, series_id: int, title: str = "") -> ViewReward:
        async with self.connection(True) as db:
            user = await self.require_user(db,user_id,True)
            existing = await self.one(db,"""SELECT reward_eligible,EXISTS(SELECT 1 FROM points_ledger WHERE user_id=? AND series_id=? AND operation='view') AS awarded
                FROM confirmed_views WHERE user_id=? AND series_id=?""",(user_id,series_id,user_id,series_id))
            if existing:
                return ViewReward(False,0,user["points_balance"],not bool(existing["reward_eligible"]) and not bool(existing["awarded"]))
            reward = (await self._settings(db))["view_reward"]
            await db.execute("INSERT INTO confirmed_views(user_id,series_id,confirmed_at,reward_eligible) VALUES (?,?,?,1)",(user_id,series_id,now()))
            entry = await self._post(db,user_id,"view",reward,"view",f"view:{user_id}:{series_id}",series_id=series_id,title=title)
            return ViewReward(True,reward,entry["balance_after"])

    async def adjust(self, actor: int, user_id: int, amount: int, reason: str, key: str):
        if type(amount) is not int or amount == 0:
            raise EconomyError("invalid")
        async with self.connection(True) as db:
            await self.require_admin(db,actor)
            return await self._post(db,user_id,"admin_credit" if amount>0 else "admin_debit",amount,reason,key,actor)

    @staticmethod
    def metric(settings):
        return "points_balance" if settings["leaderboard"]=="balance" else "points_earned"

    async def profile(self,user_id:int) -> dict:
        async with self.connection() as db:
            metric=self.metric(await self._settings(db))
            row=await self.one(db,f"""SELECT u.*,
                (SELECT COUNT(*) FROM confirmed_views WHERE user_id=u.telegram_id) AS watched,
                (SELECT COUNT(*) FROM favorites WHERE user_id=u.telegram_id) AS favorites,
                (SELECT COUNT(*) FROM ratings WHERE user_id=u.telegram_id) AS ratings,
                (SELECT COUNT(*) FROM reviews WHERE user_id=u.telegram_id AND status='published') AS reviews,
                CASE WHEN u.is_blocked=0 THEN 1+(SELECT COUNT(*) FROM users x WHERE x.is_blocked=0 AND
                (x.{metric}>u.{metric} OR (x.{metric}=u.{metric} AND x.telegram_id<u.telegram_id))) END AS rank
                FROM users u WHERE telegram_id=?""",(user_id,))
            if not row:
                raise EconomyError("missing")
            return row

    async def leaderboard(self,user_id:int,page:int=0):
        async with self.connection() as db:
            settings=await self._settings(db)
            metric=self.metric(settings)
            count=(await self.one(db,"SELECT COUNT(*) AS n FROM users WHERE is_blocked=0"))["n"]
            pages=max(1,(count+9)//10)
            page=min(max(0,page),pages-1)
            async with db.execute(f"SELECT first_name,last_name,username,{metric} AS points FROM users WHERE is_blocked=0 ORDER BY {metric} DESC,telegram_id LIMIT 10 OFFSET ?",(page*10,)) as cursor:
                rows=[dict(row) for row in await cursor.fetchall()]
            own=await self.one(db,f"""SELECT {metric} AS points,CASE WHEN u.is_blocked=0 THEN 1+
                (SELECT COUNT(*) FROM users x WHERE x.is_blocked=0 AND
                (x.{metric}>u.{metric} OR (x.{metric}=u.{metric} AND x.telegram_id<u.telegram_id))) END AS rank FROM users u WHERE telegram_id=?""",(user_id,))
            return rows,page,pages,own,settings["leaderboard"]

    async def history(self,user_id:int,page:int=0):
        async with self.connection() as db:
            count=(await self.one(db,"SELECT COUNT(*) AS n FROM points_ledger WHERE user_id=?",(user_id,)))["n"]
            pages=max(1,(count+7)//8)
            page=min(max(0,page),pages-1)
            async with db.execute("SELECT * FROM points_ledger WHERE user_id=? ORDER BY id DESC LIMIT 8 OFFSET ?",(user_id,page*8)) as cursor:
                return [dict(row) for row in await cursor.fetchall()],page,pages

    async def personal_items(self,user_id:int,section:str,page:int=0):
        queries={"watched":("confirmed_views","confirmed_at"),"favorites":("favorites","created_at"),"ratings":("ratings","updated_at"),"reviews":("reviews","updated_at")}
        if section not in queries:
            raise EconomyError("invalid")
        table,date=queries[section]
        async with self.connection() as db:
            count=(await self.one(db,f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=?",(user_id,)))["n"]
            pages=max(1,(count+7)//8)
            page=min(max(0,page),pages-1)
            async with db.execute(f"SELECT * FROM {table} WHERE user_id=? ORDER BY {date} DESC,series_id LIMIT 8 OFFSET ?",(user_id,page*8)) as cursor:
                return [dict(row) for row in await cursor.fetchall()],page,pages

    async def statistics(self):
        async with self.connection() as db:
            return await self.one(db,"""SELECT COUNT(*) AS operations,COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS credited,
                COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS debited FROM points_ledger""")

    async def suspicious(self,page=0):
        # Audit inconsistencies and unusual daily volume; no automatic sanctions.
        async with self.connection() as db:
            async with db.execute("""SELECT u.telegram_id,u.points_balance,u.points_earned,u.points_spent,
                COALESCE(SUM(l.amount),0) AS ledger_balance,
                COALESCE(SUM(CASE WHEN l.amount>0 THEN l.amount ELSE 0 END),0) AS ledger_earned,
                COALESCE(SUM(CASE WHEN l.amount<0 THEN -l.amount ELSE 0 END),0) AS ledger_spent,
                SUM(CASE WHEN l.created_at>=strftime('%Y-%m-%dT00:00:00','now') THEN 1 ELSE 0 END) AS daily
                FROM users u LEFT JOIN points_ledger l ON l.user_id=u.telegram_id GROUP BY u.telegram_id
                HAVING points_balance!=ledger_balance OR points_earned!=ledger_earned OR points_spent!=ledger_spent OR daily>=100
                ORDER BY u.telegram_id LIMIT 9 OFFSET ?""",(max(0,page)*8,)) as cursor:
                rows=[dict(row) for row in await cursor.fetchall()]
                return rows[:8],len(rows)>8

    @staticmethod
    async def _legacy_preview(db):
        amount=(await EconomyRepository._settings(db))["view_reward"]
        digest=hashlib.sha256(str(amount).encode())
        views=0
        users=0
        last_user=None
        async with db.execute("""SELECT v.user_id,v.series_id,v.confirmed_at FROM confirmed_views v
            JOIN users u ON u.telegram_id=v.user_id WHERE v.reward_eligible=0 AND u.is_blocked=0
            AND NOT EXISTS(SELECT 1 FROM points_ledger l WHERE l.operation='view' AND l.user_id=v.user_id AND l.series_id=v.series_id)
            ORDER BY v.user_id,v.series_id""") as cursor:
            async for row in cursor:
                views+=1
                if row[0]!=last_user:
                    users+=1
                    last_user=row[0]
                digest.update(json.dumps(tuple(row),ensure_ascii=True).encode())
        return {"users":users,"views":views,"amount":amount,"total":views*amount,"token":digest.hexdigest()}

    async def legacy_preview(self,actor:int):
        async with self.connection() as db:
            await self.require_admin(db,actor)
            return await self._legacy_preview(db)

    async def legacy_apply(self,actor:int,expected:dict):
        async with self.connection(True) as db:
            await self.require_admin(db,actor)
            # One immutable audit token makes repeated confirmation a no-op.
            previous=await self.one(db,"SELECT id FROM economy_audit WHERE action='legacy' AND payload=?",(json.dumps(expected,sort_keys=True),))
            if previous:
                return {**expected,"applied":0}
            current=await self._legacy_preview(db)
            if current!=expected:
                raise EconomyError("stale")
            async with db.execute("""SELECT v.user_id,v.series_id FROM confirmed_views v JOIN users u ON u.telegram_id=v.user_id
                WHERE v.reward_eligible=0 AND u.is_blocked=0 AND NOT EXISTS
                (SELECT 1 FROM points_ledger l WHERE l.operation='view' AND l.user_id=v.user_id AND l.series_id=v.series_id)
                ORDER BY v.user_id,v.series_id""") as cursor:
                # Fetch bounded batches; all rewards and the audit commit atomically.
                while rows:=await cursor.fetchmany(100):
                    for row in rows:
                        await self._post(db,row[0],"view",current["amount"],"legacy_view",f"view:{row[0]}:{row[1]}",actor,row[1])
            await db.execute("INSERT INTO economy_audit(actor_id,action,payload,created_at) VALUES (?,?,?,?)",(actor,"legacy",json.dumps(expected,sort_keys=True),now()))
            return {**current,"applied":current["views"]}
