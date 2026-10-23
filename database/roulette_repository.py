from __future__ import annotations

import asyncio
from datetime import datetime,timedelta,timezone
import json
import logging
import math
import secrets
import sqlite3

import aiosqlite

from database.economy_repository import EconomyRepository,EconomyError

logger=logging.getLogger(__name__)


class RouletteError(EconomyError):
    def __init__(self,code,remaining=0):
        super().__init__(code)
        self.remaining=remaining


def validate_prizes(prizes:list[dict]):
    if not isinstance(prizes,list) or not 1<=len(prizes)<=20:
        raise RouletteError("config")
    seen=set()
    ids=set()
    for row in prizes:
        if not isinstance(row,dict) or set(row)-{"id","points","weight","enabled"} or not {"points","weight","enabled"}<=row.keys():
            raise RouletteError("config")
        if type(row["points"]) is not int or not 0<=row["points"]<=1000000 or row["points"] in seen:
            raise RouletteError("config")
        if type(row["weight"]) is not int or not 0<=row["weight"]<=100 or type(row["enabled"]) is not bool or row["enabled"] and row["weight"]==0:
            raise RouletteError("config")
        seen.add(row["points"])
        if row.get("id") is not None:
            if type(row["id"]) is not int or row["id"]<=0 or row["id"] in ids:
                raise RouletteError("config")
            ids.add(row["id"])
    if sum(row["weight"] for row in prizes if row["enabled"])!=100:
        raise RouletteError("config")


def weighted_prize(prizes,randbelow=secrets.randbelow):
    active=[row for row in prizes if row["enabled"]]
    # Same validator for persisted and administrative configuration.
    validate_prizes([{key:row[key] for key in ("id","points","weight","enabled") if key in row} for row in prizes])
    draw=randbelow(100)
    if type(draw) is not int or not 0<=draw<100:
        raise RouletteError("config")
    cumulative=0
    for row in active:
        cumulative+=row["weight"]
        if draw<cumulative:
            return row
    raise RouletteError("config")


class RouletteRepository(EconomyRepository):
    def __init__(self,db_path=None,clock=None,randbelow=None):
        super().__init__(db_path)
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.randbelow=randbelow or secrets.randbelow

    def timestamp(self):
        value=self.clock()
        if value.tzinfo is None:
            raise RouletteError("invalid")
        return value.astimezone(timezone.utc)

    @staticmethod
    def remaining(deadline,timestamp):
        return max(0,math.ceil((datetime.fromisoformat(deadline)-timestamp).total_seconds())) if deadline else 0

    async def config(self):
        async with self.connection() as db:
            return await self._config(db)

    @staticmethod
    async def _config(db):
        settings=await EconomyRepository.one(db,"SELECT * FROM roulette_settings WHERE id=1")
        async with db.execute("SELECT * FROM roulette_prizes WHERE deleted=0 ORDER BY id") as cursor:
            settings["prizes"]=[{**dict(row),"enabled":bool(row["enabled"])} for row in await cursor.fetchall()]
        return settings

    async def status(self,user_id):
        async with self.connection() as db:
            row=await self.one(db,"""SELECT u.points_balance AS balance,
                (SELECT COUNT(*) FROM roulette_spins WHERE user_id=u.telegram_id) AS spins,
                (SELECT COALESCE(SUM(awarded_points),0) FROM roulette_spins WHERE user_id=u.telegram_id) AS winnings,
                (SELECT awarded_points FROM roulette_spins WHERE user_id=u.telegram_id ORDER BY id DESC LIMIT 1) AS last_prize,
                (SELECT next_available_at FROM roulette_spins WHERE user_id=u.telegram_id ORDER BY id DESC LIMIT 1) AS next_available_at,
                (SELECT enabled FROM roulette_settings WHERE id=1) AS enabled
                FROM users u WHERE telegram_id=?""",(user_id,))
            if not row:
                raise RouletteError("missing")
            row["remaining"]=self.remaining(row["next_available_at"],self.timestamp())
            return row

    async def spin(self,user_id:int,request_key:str):
        if not isinstance(request_key,str) or not 1<=len(request_key)<=200:
            raise RouletteError("invalid")
        for attempt in range(3):
            try:
                result=await self._spin(user_id,request_key)
                logger.info("Roulette result committed (replayed=%s).",result["replayed"])
                return result
            except aiosqlite.OperationalError as exc:
                code=getattr(exc,"sqlite_errorcode",None)
                if code is None or code & 255 not in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}:
                    raise
                logger.warning("Roulette storage busy; retry %s.",attempt+1)
                if attempt==2:
                    raise RouletteError("busy") from None
                await asyncio.sleep(.02*(attempt+1))

    async def _spin(self,user_id,request_key):
        async with self.connection(True,timeout=.25) as db:
            user=await self.require_user(db,user_id,True)
            timestamp=self.timestamp()
            previous=await self.one(db,"SELECT * FROM roulette_spins WHERE request_key=?",(request_key,))
            last=await self.one(db,"SELECT next_available_at FROM roulette_spins WHERE user_id=? ORDER BY id DESC LIMIT 1",(user_id,))
            if previous:
                if previous["user_id"]!=user_id:
                    raise RouletteError("invalid")
                return {**previous,"replayed":True,"balance":user["points_balance"],"remaining":self.remaining(last["next_available_at"],timestamp)}
            config=await self._config(db)
            if not config["enabled"]:
                raise RouletteError("disabled")
            remaining=self.remaining(last["next_available_at"],timestamp) if last else 0
            if remaining:
                raise RouletteError("cooldown",remaining)
            prize=weighted_prize(config["prizes"],self.randbelow)
            deadline=timestamp+timedelta(seconds=config["cooldown_seconds"])
            entry=await self._post(db,user_id,"roulette_reward",prize["points"],"roulette",f"roulette:{request_key}")
            cursor=await db.execute("""INSERT INTO roulette_spins(user_id,prize_id,awarded_points,spun_at,next_available_at,transaction_id,request_key)
                VALUES (?,?,?,?,?,?,?)""",(user_id,prize["id"],prize["points"],timestamp.isoformat(),deadline.isoformat(),entry["id"],request_key))
            spin=await self.one(db,"SELECT * FROM roulette_spins WHERE id=?",(cursor.lastrowid,))
            await cursor.close()
            return {**spin,"replayed":False,"balance":entry["balance_after"],"remaining":config["cooldown_seconds"]}

    async def history(self,user_id,page=0):
        async with self.connection() as db:
            count=(await self.one(db,"SELECT COUNT(*) AS n FROM roulette_spins WHERE user_id=?",(user_id,)))["n"]
            pages=max(1,(count+7)//8)
            page=min(max(0,page),pages-1)
            async with db.execute("SELECT * FROM roulette_spins WHERE user_id=? ORDER BY id DESC LIMIT 8 OFFSET ?",(user_id,page*8)) as cursor:
                return [dict(row) for row in await cursor.fetchall()],page,pages

    async def save_config(self,actor,expected_version,prizes=None,enabled=None,cooldown_seconds=None):
        if prizes is not None:
            validate_prizes(prizes)
        if enabled is not None and type(enabled) is not bool:
            raise RouletteError("config")
        if cooldown_seconds is not None and (type(cooldown_seconds) is not int or not 3600<=cooldown_seconds<=2592000):
            raise RouletteError("config")
        async with self.connection(True) as db:
            await self.require_admin(db,actor)
            config=await self._config(db)
            if config["version"]!=expected_version:
                raise RouletteError("stale")
            if prizes is not None:
                existing={row["id"] for row in config["prizes"]}
                kept={row["id"] for row in prizes if row.get("id") is not None}
                if kept-existing:
                    raise RouletteError("config")
                for prize_id in existing-kept:
                    await db.execute("UPDATE roulette_prizes SET deleted=1,enabled=0,updated_at=? WHERE id=?",(self.timestamp().isoformat(),prize_id))
                for row in prizes:
                    timestamp=self.timestamp().isoformat()
                    if row.get("id") is not None:
                        await db.execute("UPDATE roulette_prizes SET points=?,weight=?,enabled=?,updated_at=? WHERE id=?",(row["points"],row["weight"],int(row["enabled"]),timestamp,row["id"]))
                    else:
                        await db.execute("INSERT INTO roulette_prizes(points,weight,enabled,created_at,updated_at) VALUES (?,?,?,?,?)",(row["points"],row["weight"],int(row["enabled"]),timestamp,timestamp))
            await db.execute("UPDATE roulette_settings SET enabled=?,cooldown_seconds=?,version=version+1 WHERE id=1",(int(enabled) if enabled is not None else config["enabled"],cooldown_seconds if cooldown_seconds is not None else config["cooldown_seconds"]))
            await db.execute("INSERT INTO economy_audit(actor_id,action,payload,created_at) VALUES (?,?,?,?)",(actor,"roulette_config",json.dumps({"prizes":prizes,"enabled":enabled,"cooldown_seconds":cooldown_seconds}),self.timestamp().isoformat()))

    async def statistics(self,page=0):
        async with self.connection() as db:
            total=await self.one(db,"SELECT COUNT(*) AS spins,COALESCE(SUM(awarded_points),0) AS points FROM roulette_spins")
            count=(await self.one(db,"SELECT COUNT(DISTINCT awarded_points) AS n FROM roulette_spins"))["n"]
            total['pages']=max(1,(count+19)//20)
            total['page']=min(max(0,page),total['pages']-1)
            async with db.execute("SELECT awarded_points,COUNT(*) AS spins FROM roulette_spins GROUP BY awarded_points ORDER BY awarded_points LIMIT 20 OFFSET ?",(total['page']*20,)) as cursor:
                total["distribution"]=[dict(row) for row in await cursor.fetchall()]
            return total
