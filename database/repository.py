from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from config import settings
from database.models import UserRecord


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


async def _execute(
    db: aiosqlite.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> None:
    cursor = await db.execute(query, parameters)
    await cursor.close()


class UserRepository:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or settings.DB_PATH)

    async def ensure_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language: str | None = None,
    ) -> UserRecord:
        now = _serialize_datetime(_utc_now())
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await _execute(
                db,
                """
                INSERT INTO users (
                    telegram_id, username, first_name, last_name,
                    is_admin, captcha_passed, language, created_at, last_activity, is_blocked
                ) VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, 0)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_activity = excluded.last_activity
                """,
                (telegram_id, username, first_name, last_name, language, now, now),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise RuntimeError("User upsert completed without returning a user row.")
        return UserRecord.from_row(dict(row))

    async def get_user_by_telegram_id(self, telegram_id: int) -> UserRecord | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return UserRecord.from_row(dict(row)) if row is not None else None

    async def set_admin(self, telegram_id: int, is_admin: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                "UPDATE users SET is_admin = ?, last_activity = ? WHERE telegram_id = ?",
                (int(is_admin), _serialize_datetime(_utc_now()), telegram_id),
            )
            await db.commit()

    async def set_captcha_passed(self, telegram_id: int, passed: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                "UPDATE users SET captcha_passed = ?, last_activity = ? WHERE telegram_id = ?",
                (int(passed), _serialize_datetime(_utc_now()), telegram_id),
            )
            await db.commit()

    async def set_language(self, telegram_id: int, language: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                "UPDATE users SET language = ?, last_activity = ? WHERE telegram_id = ?",
                (language, _serialize_datetime(_utc_now()), telegram_id),
            )
            await db.commit()

    async def update_last_activity(self, telegram_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                "UPDATE users SET last_activity = ? WHERE telegram_id = ?",
                (_serialize_datetime(_utc_now()), telegram_id),
            )
            await db.commit()

    async def get_stats(self) -> dict[str, int]:
        now = _utc_now()
        today = now.date().isoformat()
        week_ago = _serialize_datetime(now - timedelta(days=7))
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) AS total_users,
                    COALESCE(SUM(captcha_passed = 1), 0) AS captcha_passed,
                    COALESCE(SUM(is_admin = 1), 0) AS admins,
                    COALESCE(SUM(is_blocked = 1), 0) AS blocked_users,
                    COALESCE(SUM(date(created_at) = ?), 0) AS new_today,
                    COALESCE(SUM(created_at >= ?), 0) AS new_last_7_days
                FROM users
                """,
                (today, week_ago),
            )
            row = await cursor.fetchone()
            await cursor.close()
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM broadcast_history
                WHERE status = 'completed'
                """
            )
            broadcast_row = await cursor.fetchone()
            await cursor.close()
        return {
            "total_users": int(row[0]),
            "captcha_passed": int(row[1]),
            "admins": int(row[2]),
            "blocked_users": int(row[3]),
            "new_today": int(row[4]),
            "new_last_7_days": int(row[5]),
            "broadcasts": int(broadcast_row[0]),
        }

    async def get_active_user_ids(self) -> list[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT telegram_id FROM users
                WHERE telegram_id IS NOT NULL AND is_blocked = 0
                ORDER BY id
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [int(row[0]) for row in rows]

    async def set_user_blocked(self, telegram_id: int, blocked: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                "UPDATE users SET is_blocked = ? WHERE telegram_id = ?",
                (int(blocked), telegram_id),
            )
            await db.commit()

    async def get_captcha_state(self, telegram_id: int) -> dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT attempts, blocked_until, updated_at FROM captcha_attempts WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return {"attempts": 0, "blocked_until": None, "updated_at": None}
        return {"attempts": int(row[0]), "blocked_until": row[1], "updated_at": row[2]}

    async def update_captcha_state(
        self,
        telegram_id: int,
        attempts: int,
        blocked_until: str | None = None,
    ) -> None:
        now = _serialize_datetime(_utc_now())
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                """
                INSERT INTO captcha_attempts (telegram_id, attempts, blocked_until, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id)
                DO UPDATE SET attempts = excluded.attempts,
                    blocked_until = excluded.blocked_until,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, attempts, blocked_until, now),
            )
            await db.commit()

    async def increment_captcha_attempts(self, telegram_id: int) -> int:
        now = _serialize_datetime(_utc_now())
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                """
                INSERT INTO captcha_attempts
                    (telegram_id, attempts, blocked_until, updated_at)
                VALUES (?, 1, NULL, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    attempts = captcha_attempts.attempts + 1,
                    blocked_until = NULL,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, now),
            )
            cursor = await db.execute(
                "SELECT attempts FROM captcha_attempts WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            await db.commit()
        if row is None:
            raise RuntimeError("Failed to increment CAPTCHA attempts.")
        return int(row[0])

    async def reset_captcha_state(self, telegram_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                "DELETE FROM captcha_attempts WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.commit()

    async def get_admin_activation_state(self, telegram_id: int) -> dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT attempts, blocked_until FROM admin_activation_attempts
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return {"attempts": 0, "blocked_until": None}
        return {"attempts": int(row[0]), "blocked_until": row[1]}

    async def update_admin_activation_state(
        self,
        telegram_id: int,
        attempts: int,
        blocked_until: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                """
                INSERT INTO admin_activation_attempts
                    (telegram_id, attempts, blocked_until, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    attempts = excluded.attempts,
                    blocked_until = excluded.blocked_until,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_id,
                    attempts,
                    blocked_until,
                    _serialize_datetime(_utc_now()),
                ),
            )
            await db.commit()

    async def increment_admin_activation_attempts(self, telegram_id: int) -> int:
        now = _serialize_datetime(_utc_now())
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                """
                INSERT INTO admin_activation_attempts
                    (telegram_id, attempts, blocked_until, updated_at)
                VALUES (?, 1, NULL, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    attempts = admin_activation_attempts.attempts + 1,
                    blocked_until = NULL,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, now),
            )
            cursor = await db.execute(
                """
                SELECT attempts FROM admin_activation_attempts WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            await db.commit()
        if row is None:
            raise RuntimeError("Failed to increment administrator activation attempts.")
        return int(row[0])

    async def start_broadcast_history(
        self,
        text: str,
        target_count: int,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO broadcast_history
                    (created_at, text, target_count, success_count, failed_count,
                     status, processed_count, cancelled)
                VALUES (?, ?, ?, 0, 0, 'running', 0, 0)
                """,
                (
                    _serialize_datetime(_utc_now()),
                    text,
                    target_count,
                ),
            )
            run_id = int(cursor.lastrowid)
            await cursor.close()
            await db.commit()
        return run_id

    async def finish_broadcast_history(
        self,
        run_id: int,
        *,
        success_count: int,
        failed_count: int,
        processed_count: int,
        cancelled: bool,
    ) -> None:
        status = "cancelled" if cancelled else "completed"
        async with aiosqlite.connect(self.db_path) as db:
            await _execute(
                db,
                """
                UPDATE broadcast_history
                SET success_count = ?, failed_count = ?, processed_count = ?,
                    cancelled = ?, status = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    success_count,
                    failed_count,
                    processed_count,
                    int(cancelled),
                    status,
                    run_id,
                ),
            )
            await db.commit()

    async def has_unfinished_broadcast(self) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM broadcast_history WHERE status = 'running' LIMIT 1"
            )
            row = await cursor.fetchone()
            await cursor.close()
        return row is not None

    async def mark_broadcasts_interrupted(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE broadcast_history
                SET status = 'interrupted', cancelled = 1
                WHERE status = 'running'
                """
            )
            changed = cursor.rowcount
            await cursor.close()
            await db.commit()
        return changed
