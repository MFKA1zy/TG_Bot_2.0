from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import time

import aiosqlite

from config import settings

DEFAULT_POLICY = {"auto_safe": False, "forbidden_action": "pending", "auto_ban": False,
                  "warning_threshold": 3, "min_votes": 3, "review_cooldown": 30}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ReviewError(ValueError):
    """Stable error codes for localized presentation."""


class ReviewsRepository:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or settings.DB_PATH)

    @asynccontextmanager
    async def connection(self, write=False, timeout=10):
        async with aiosqlite.connect(self.db_path, timeout=timeout) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            if write:
                await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                if write:
                    await db.commit()
            except BaseException:
                if write:
                    await db.rollback()
                raise

    @staticmethod
    async def one(db, sql, args=()):
        async with db.execute(sql, args) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def policy(self) -> dict:
        async with self.connection() as db:
            async with db.execute("SELECT key,value FROM review_settings") as cursor:
                return {**DEFAULT_POLICY, **{row[0]: json.loads(row[1]) for row in await cursor.fetchall()}}

    async def words(self) -> list[str]:
        async with self.connection() as db:
            async with db.execute("SELECT phrase FROM moderation_words ORDER BY phrase") as cursor:
                return [row[0] for row in await cursor.fetchall()]

    async def rating(self, user_id: int, series_id: int, score: int, request_key: str | None = None) -> None:
        if type(score) is not int or not 1 <= score <= 10:
            raise ReviewError("invalid")
        async with self.connection(True) as db:
            await self._active_user(db, user_id)
            if request_key:
                previous = await self.one(db, "SELECT * FROM rating_requests WHERE request_key=?", (request_key,))
                if previous:
                    if (previous["user_id"], previous["series_id"], previous["score"]) != (user_id, series_id, score):
                        raise ReviewError("invalid")
                    return
                await db.execute("INSERT INTO rating_requests VALUES (?,?,?,?)", (request_key, user_id, series_id, score))
            await db.execute("INSERT INTO ratings VALUES (?,?,?,?) ON CONFLICT(user_id,series_id) DO UPDATE SET score=excluded.score,updated_at=excluded.updated_at", (user_id, series_id, score, now()))

    async def rating_summary(self, series_id: int, user_id: int) -> dict:
        async with self.connection() as db:
            return await self.one(db, "SELECT COUNT(*) AS votes, COALESCE(AVG(score),0) AS average, MAX(CASE WHEN user_id=? THEN score END) AS own FROM ratings WHERE series_id=?", (user_id, series_id))

    async def aggregates(self, series_ids: list[int] | None = None) -> dict[int, dict]:
        if series_ids == []:
            return {}
        clause = " WHERE series_id IN (" + ",".join("?" for _ in series_ids) + ")" if series_ids is not None else ""
        async with self.connection() as db:
            async with db.execute("SELECT series_id,COUNT(*) AS votes,AVG(score) AS average FROM ratings" + clause + " GROUP BY series_id", series_ids or ()) as cursor:
                return {row["series_id"]: dict(row) for row in await cursor.fetchall()}

    async def review(self, review_id: int) -> dict | None:
        async with self.connection() as db:
            return await self.one(db, "SELECT r.*,COALESCE(u.first_name,u.username,'—') AS author FROM reviews r JOIN users u ON u.telegram_id=r.user_id WHERE r.id=?", (review_id,))

    async def own_review(self, user_id: int, series_id: int) -> dict | None:
        async with self.connection() as db:
            return await self.one(db, "SELECT * FROM reviews WHERE user_id=? AND series_id=?", (user_id, series_id))

    async def submit(self, user_id: int, series_id: int, text: str, status: str, reasons: list[str], request_key: str, cooldown: int) -> dict:
        async with self.connection(True) as db:
            await self._active_user(db, user_id)
            previous = await self.one(db, "SELECT * FROM review_submissions WHERE request_key=?", (request_key,))
            if previous:
                if (previous["user_id"], previous["series_id"]) != (user_id, series_id):
                    raise ReviewError("invalid")
                return await self.one(db, "SELECT * FROM reviews WHERE id=?", (previous["review_id"],))
            limit = await self.one(db, "SELECT submitted_at FROM review_submission_limits WHERE user_id=?", (user_id,))
            if limit and time.time() - limit["submitted_at"] < cooldown:
                raise ReviewError("cooldown")
            existing = await self.one(db, "SELECT * FROM reviews WHERE user_id=? AND series_id=?", (user_id, series_id))
            if existing and existing["status"] != "deleted" and status == "published":
                status = "pending"
            timestamp = now()
            await db.execute("""INSERT INTO reviews(user_id,series_id,text,status,reasons,created_at,updated_at,published_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user_id,series_id) DO UPDATE SET
                text=excluded.text,status=excluded.status,reasons=excluded.reasons,updated_at=excluded.updated_at,
                version=reviews.version+1,published_at=excluded.published_at""",
                (user_id, series_id, text, status, json.dumps(reasons), timestamp, timestamp, timestamp if status == "published" else None))
            result = await self.one(db, "SELECT * FROM reviews WHERE user_id=? AND series_id=?", (user_id, series_id))
            await db.execute("INSERT INTO review_submissions VALUES (?,?,?,?)", (request_key, user_id, series_id, result["id"]))
            await db.execute("INSERT INTO review_submission_limits VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET submitted_at=excluded.submitted_at", (user_id, time.time()))
            await self.event(db, user_id, user_id, result["id"], "edit" if existing else "submit", ",".join(reasons), {"text": text, "version": result["version"], "status": status}, request_key)
            return result

    @staticmethod
    async def _active_user(db, user_id):
        row = await ReviewsRepository.one(db, "SELECT is_blocked FROM users WHERE telegram_id=?", (user_id,))
        if not row or row["is_blocked"]:
            raise ReviewError("access")

    @staticmethod
    async def event(db, actor, subject, review_id, action, reason, payload=None, key=None):
        await db.execute("INSERT INTO review_moderation_events(actor_id,subject_id,review_id,action,reason,payload,created_at,request_key) VALUES (?,?,?,?,?,?,?,?)", (actor, subject, review_id, action, reason, json.dumps(payload or {}, ensure_ascii=False), now(), key))

    async def delete_own(self, user_id: int, review_id: int, version: int) -> None:
        async with self.connection(True) as db:
            await self._active_user(db, user_id)
            row = await self.one(db, "SELECT * FROM reviews WHERE id=? AND user_id=?", (review_id, user_id))
            if not row:
                raise ReviewError("missing")
            if row["status"] == "deleted":
                return
            if row["version"] != version:
                raise ReviewError("stale")
            await db.execute("UPDATE reviews SET status='deleted',version=version+1,updated_at=? WHERE id=?", (now(), review_id))
            await self.event(db, user_id, user_id, review_id, "author_delete", "", {"version": version})

    async def public_reviews(self, series_id: int, page: int = 0) -> tuple[list[dict], int, int]:
        async with self.connection() as db:
            count = (await self.one(db, "SELECT COUNT(*) AS n FROM reviews r JOIN users u ON u.telegram_id=r.user_id WHERE series_id=? AND status='published' AND u.is_blocked=0", (series_id,)))["n"]
            pages = max(1, count)
            page = min(max(0, page), pages - 1)
            async with db.execute("""SELECT r.*,COALESCE(u.first_name,u.username,'—') AS author, a.score
                FROM reviews r JOIN users u ON u.telegram_id=r.user_id
                LEFT JOIN ratings a ON a.user_id=r.user_id AND a.series_id=r.series_id
                WHERE r.series_id=? AND r.status='published' AND u.is_blocked=0
                ORDER BY r.published_at DESC,r.id DESC LIMIT 1 OFFSET ?""", (series_id, page)) as cursor:
                return [dict(row) for row in await cursor.fetchall()], page, pages

    async def report(self, user_id: int, review_id: int, reason: str) -> None:
        if reason not in {"abuse", "spam", "content", "other"}:
            raise ReviewError("invalid")
        async with self.connection(True) as db:
            await self._active_user(db, user_id)
            review = await self.one(db, "SELECT * FROM reviews WHERE id=? AND status='published'", (review_id,))
            if not review or review["user_id"] == user_id:
                raise ReviewError("invalid")
            await db.execute("INSERT OR IGNORE INTO review_reports(review_id,user_id,reason,created_at) VALUES (?,?,?,?)", (review_id, user_id, reason, now()))

    async def admin_page(self, section: str, page=0, search="") -> tuple[list[dict], int, int]:
        async with self.connection() as db:
            if section == "reports":
                table, clause, args = "review_reports", "status='open'", ()
            else:
                table = "reviews"
                if section == "search":
                    await db.create_function("casefold", 1, lambda text: str(text).casefold(), deterministic=True)
                    clause, args = "instr(casefold(text),casefold(?))>0", (search,)
                elif section in {"pending", "published", "rejected", "deleted"}:
                    clause, args = "status=?", (section,)
                else:
                    raise ReviewError("invalid")
            count = (await self.one(db, f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}", args))["n"]
            pages = max(1, (count + 7) // 8)
            page = min(max(0, page), pages - 1)
            async with db.execute(f"SELECT * FROM {table} WHERE {clause} ORDER BY id DESC LIMIT 8 OFFSET ?", (*args, page * 8)) as cursor:
                return [dict(row) for row in await cursor.fetchall()], page, pages

    async def user_history(self, user_id: int, page: int = 0) -> dict:
        async with self.connection() as db:
            result = await self.one(db, "SELECT telegram_id,first_name,username,is_blocked FROM users WHERE telegram_id=?", (user_id,))
            if not result:
                raise ReviewError("missing")
            count = (await self.one(db, """SELECT MAX(n) AS n FROM (
                SELECT COUNT(*) AS n FROM review_moderation_events WHERE subject_id=? UNION ALL
                SELECT COUNT(*) FROM user_warnings WHERE user_id=? UNION ALL
                SELECT COUNT(*) FROM user_bans WHERE user_id=? UNION ALL
                SELECT COUNT(*) FROM ratings WHERE user_id=?)""", (user_id,user_id,user_id,user_id)))["n"]
            result["pages"] = max(1, (count + 9) // 10)
            result["page"] = min(max(0, page), result["pages"] - 1)
            for name, query in (
                ("warnings", "SELECT * FROM user_warnings WHERE user_id=? ORDER BY id DESC LIMIT 10"),
                ("bans", "SELECT * FROM user_bans WHERE user_id=? ORDER BY id DESC LIMIT 10"),
                ("events", "SELECT * FROM review_moderation_events WHERE subject_id=? ORDER BY id DESC LIMIT 10")):
                async with db.execute(query + " OFFSET ?", (user_id, result["page"] * 10)) as cursor:
                    result[name] = [dict(row) for row in await cursor.fetchall()]
            async with db.execute("SELECT series_id,score FROM ratings WHERE user_id=? ORDER BY series_id LIMIT 10 OFFSET ?", (user_id, result["page"] * 10)) as cursor:
                result["ratings"] = [dict(row) for row in await cursor.fetchall()]
            return result

    async def stats(self) -> dict:
        async with self.connection() as db:
            return await self.one(db, """SELECT (SELECT COUNT(*) FROM ratings) AS ratings,
                (SELECT COUNT(*) FROM reviews) AS reviews,(SELECT COUNT(*) FROM reviews WHERE status='pending') AS pending,
                (SELECT COUNT(*) FROM review_reports WHERE status='open') AS reports,
                (SELECT COUNT(*) FROM user_warnings WHERE revoked_at IS NULL) AS warnings""")

    async def admin_action(self, actor: int, action: str, target: int, reason: str, request_key: str, version: int = 0, extra: int = 0) -> None:
        if not 1 <= len(reason) <= 500:
            raise ReviewError("invalid")
        async with self.connection(True) as db:
            admin = await self.one(db, "SELECT is_admin,is_blocked FROM users WHERE telegram_id=?", (actor,))
            if not admin or not admin["is_admin"] or admin["is_blocked"]:
                raise ReviewError("access")
            if await self.one(db, "SELECT id FROM review_moderation_events WHERE request_key=?", (request_key,)):
                return
            subject, review_id = target, None
            if action in {"published", "rejected", "deleted"}:
                row = await self.one(db, "SELECT * FROM reviews WHERE id=?", (target,))
                if not row:
                    raise ReviewError("missing")
                if row["version"] != version:
                    raise ReviewError("stale")
                subject, review_id = row["user_id"], target
                await db.execute("UPDATE reviews SET status=?,version=version+1,updated_at=?,published_at=? WHERE id=?", (action, now(), now() if action == "published" else None, target))
                if action in {"rejected", "deleted"}:
                    await db.execute("UPDATE review_reports SET status='resolved' WHERE review_id=? AND status='open'", (target,))
            elif action == "dismiss":
                row = await self.one(db, "SELECT p.review_id,r.user_id FROM review_reports p JOIN reviews r ON r.id=p.review_id WHERE p.id=?", (target,))
                if not row:
                    raise ReviewError("missing")
                subject, review_id = row["user_id"], row["review_id"]
                await db.execute("UPDATE review_reports SET status='dismissed' WHERE id=?", (target,))
            elif action == "rating_delete":
                await db.execute("DELETE FROM ratings WHERE user_id=? AND series_id=?", (target, extra))
            elif action in {"warn", "ban", "unban", "revoke"}:
                if action == "revoke":
                    row = await self.one(db, "SELECT user_id FROM user_warnings WHERE id=?", (target,))
                    if not row:
                        raise ReviewError("missing")
                    subject = row["user_id"]
                    await db.execute("UPDATE user_warnings SET revoked_at=? WHERE id=?", (now(), target))
                else:
                    if not await self.one(db, "SELECT telegram_id FROM users WHERE telegram_id=?", (target,)):
                        raise ReviewError("missing")
                    if action in {"warn", "ban"} and (target in settings.ADMIN_IDS or target == actor):
                        raise ReviewError("protected")
                    if action == "warn":
                        await db.execute("INSERT INTO user_warnings(user_id,actor_id,reason,created_at) VALUES (?,?,?,?)", (target, actor, reason, now()))
                        async with db.execute("SELECT key,value FROM review_settings") as cursor:
                            policy = {**DEFAULT_POLICY, **{row[0]: json.loads(row[1]) for row in await cursor.fetchall()}}
                        count = (await self.one(db, "SELECT COUNT(*) AS n FROM user_warnings WHERE user_id=? AND revoked_at IS NULL", (target,)))["n"]
                        if policy["auto_ban"] and count >= policy["warning_threshold"]:
                            await self._ban(db, actor, target, reason)
                            await self.event(db, actor, target, None, "auto_ban", reason)
                    elif action == "ban":
                        await self._ban(db, actor, target, reason)
                    else:
                        await db.execute("UPDATE users SET is_blocked=0 WHERE telegram_id=?", (target,))
                        await db.execute("UPDATE user_bans SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now(), target))
            else:
                raise ReviewError("invalid")
            await self.event(db, actor, subject, review_id, action, reason, {"target": target, "version": version, "series_id": extra}, request_key)

    @staticmethod
    async def _ban(db, actor, target, reason):
        await db.execute("UPDATE users SET is_blocked=1 WHERE telegram_id=?", (target,))
        await db.execute("INSERT INTO user_bans(user_id,actor_id,reason,created_at) VALUES (?,?,?,?)", (target, actor, reason, now()))

    async def configure(self, actor: int, values: dict, words: list[str] | None = None):
        for key, value in values.items():
            if key not in DEFAULT_POLICY:
                raise ReviewError("invalid")
            if key in {"auto_safe", "auto_ban"} and type(value) is not bool:
                raise ReviewError("invalid")
            if key in {"warning_threshold", "min_votes", "review_cooldown"} and (type(value) is not int or not 1 <= value <= 10000):
                raise ReviewError("invalid")
            if key == "forbidden_action" and value not in {"pending", "rejected"}:
                raise ReviewError("invalid")
        if words is not None and (len(words) > 500 or any(not 1 <= len(word) <= 100 for word in words)):
            raise ReviewError("invalid")
        async with self.connection(True) as db:
            admin = await self.one(db, "SELECT is_admin,is_blocked FROM users WHERE telegram_id=?", (actor,))
            if not admin or not admin["is_admin"] or admin["is_blocked"]:
                raise ReviewError("access")
            for key, value in values.items():
                await db.execute("INSERT INTO review_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            if words is not None:
                await db.execute("DELETE FROM moderation_words")
                await db.executemany("INSERT OR IGNORE INTO moderation_words VALUES (?)", [(word,) for word in words])
            await self.event(db, actor, actor, None, "configure", "", {"settings": values, "words": words})

    async def change_word(self, actor: int, phrase: str, enabled: bool):
        if not 1 <= len(phrase) <= 100:
            raise ReviewError("invalid")
        async with self.connection(True) as db:
            admin = await self.one(db, "SELECT is_admin,is_blocked FROM users WHERE telegram_id=?", (actor,))
            if not admin or not admin["is_admin"] or admin["is_blocked"]:
                raise ReviewError("access")
            if enabled:
                count = (await self.one(db, "SELECT COUNT(*) AS n FROM moderation_words"))["n"]
                if count >= 500:
                    raise ReviewError("invalid")
                await db.execute("INSERT OR IGNORE INTO moderation_words VALUES (?)", (phrase,))
            else:
                await db.execute("DELETE FROM moderation_words WHERE phrase=?", (phrase,))
            await self.event(db, actor, actor, None, "word_add" if enabled else "word_remove", phrase)
