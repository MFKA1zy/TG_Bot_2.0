from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from config import settings


async def _execute(db: aiosqlite.Connection, sql: str) -> None:
    cursor = await db.execute(sql)
    await cursor.close()


async def _backup_database(
    db: aiosqlite.Connection,
    database_path: Path,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = database_path.with_name(
        f"{database_path.name}.pre-migration.{timestamp}.bak"
    )
    suffix = 1
    while backup_path.exists():
        backup_path = database_path.with_name(
            f"{database_path.name}.pre-migration.{timestamp}.{suffix}.bak"
        )
        suffix += 1
    async with aiosqlite.connect(backup_path) as backup_db:
        await db.backup(backup_db)
    return backup_path


async def init_db(db_path: str | Path | None = None) -> None:
    database_path = Path(db_path or settings.DB_PATH)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'broadcast_history'"
        )
        history_exists = await cursor.fetchone() is not None
        await cursor.close()
        if history_exists:
            cursor = await db.execute("PRAGMA table_info(broadcast_history)")
            columns = {row[1] for row in await cursor.fetchall()}
            await cursor.close()
            required_columns = {"status", "processed_count", "cancelled"}
            if not required_columns.issubset(columns):
                await _backup_database(db, database_path)

        cursor = await db.execute("PRAGMA journal_mode=WAL;")
        await cursor.fetchone()
        await cursor.close()
        await _execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin INTEGER DEFAULT 0,
                captcha_passed INTEGER DEFAULT 0,
                language TEXT,
                created_at TEXT,
                last_activity TEXT,
                is_blocked INTEGER DEFAULT 0
            )
            """
        )
        cursor = await db.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "language" not in user_columns:
            await _execute(db, "ALTER TABLE users ADD COLUMN language TEXT")
        await _execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS captcha_attempts (
                telegram_id INTEGER PRIMARY KEY,
                attempts INTEGER DEFAULT 0,
                blocked_until TEXT,
                updated_at TEXT
            )
            """
        )
        await _execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS broadcast_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                text TEXT,
                target_count INTEGER,
                success_count INTEGER,
                failed_count INTEGER,
                status TEXT NOT NULL DEFAULT 'completed',
                processed_count INTEGER NOT NULL DEFAULT 0,
                cancelled INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cursor = await db.execute("PRAGMA table_info(broadcast_history)")
        broadcast_columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "status" not in broadcast_columns:
            await _execute(
                db,
                "ALTER TABLE broadcast_history ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
            )
        if "processed_count" not in broadcast_columns:
            await _execute(
                db,
                "ALTER TABLE broadcast_history ADD COLUMN processed_count INTEGER NOT NULL DEFAULT 0"
            )
        if "cancelled" not in broadcast_columns:
            await _execute(
                db,
                "ALTER TABLE broadcast_history ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0"
            )
        await _execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS admin_activation_attempts (
                telegram_id INTEGER PRIMARY KEY,
                attempts INTEGER DEFAULT 0,
                blocked_until TEXT,
                updated_at TEXT
            )
            """
        )
        await db.commit()
