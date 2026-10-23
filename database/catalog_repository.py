from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from config import settings


async def create_catalog_tables(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            position INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL, series_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, series_id)
        );
        CREATE TABLE IF NOT EXISTS confirmed_views (
            user_id INTEGER NOT NULL, series_id INTEGER NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, series_id)
        );
        CREATE INDEX IF NOT EXISTS views_series_date ON confirmed_views(series_id, confirmed_at);
        CREATE INDEX IF NOT EXISTS views_date ON confirmed_views(confirmed_at);
        CREATE TABLE IF NOT EXISTS catalog_settings (
            key TEXT PRIMARY KEY, value INTEGER NOT NULL
        );
    """)


class CatalogRepository:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or settings.DB_PATH)

    async def card_state(self, user_id: int, series_id: int) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""SELECT
                EXISTS(SELECT 1 FROM favorites WHERE user_id=? AND series_id=?) AS favorite,
                EXISTS(SELECT 1 FROM confirmed_views WHERE user_id=? AND series_id=?) AS watched,
                (SELECT COUNT(*) FROM confirmed_views WHERE series_id=?) AS views,
                (SELECT COALESCE(AVG(score),0) FROM ratings WHERE series_id=?) AS average,
                (SELECT COUNT(*) FROM ratings WHERE series_id=?) AS votes,
                (SELECT score FROM ratings WHERE user_id=? AND series_id=?) AS own""",
                (user_id,series_id,user_id,series_id,series_id,series_id,series_id,user_id,series_id)) as cursor:
                return dict(await cursor.fetchone())

    async def categories(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM categories ORDER BY position, id") as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def save_category(self, name: str, position: int = 0, category_id: int | None = None) -> int:
        name = " ".join(name.split())
        if not 1 <= len(name) <= 60 or type(position) is not int or not -100000 <= position <= 100000:
            raise ValueError("Invalid category")
        async with aiosqlite.connect(self.db_path) as db:
            try:
                if category_id is None:
                    cursor = await db.execute("INSERT INTO categories(name, normalized_name, position) VALUES (?, ?, ?)", (name, name.casefold(), position))
                    result = cursor.lastrowid
                else:
                    cursor = await db.execute("UPDATE categories SET name=?, normalized_name=?, position=? WHERE id=?", (name, name.casefold(), position, category_id))
                    if cursor.rowcount != 1:
                        raise ValueError("Category not found")
                    result = category_id
                await cursor.close()
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                raise ValueError("Duplicate category") from exc
        return int(result)

    async def delete_category(self, category_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM categories WHERE id=?", (category_id,))
            await db.commit()

    async def favorite_ids(self, user_id: int) -> set[int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT series_id FROM favorites WHERE user_id=?", (user_id,)) as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def set_favorite(self, user_id: int, series_id: int, enabled: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            if enabled:
                await db.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?, ?)", (user_id, series_id, datetime.now(timezone.utc).isoformat()))
            else:
                await db.execute("DELETE FROM favorites WHERE user_id=? AND series_id=?", (user_id, series_id))
            await db.commit()

    async def confirm_view(self, user_id: int, series_id: int) -> bool:
        """Self-confirmation only; no points are awarded in this phase."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("INSERT OR IGNORE INTO confirmed_views VALUES (?, ?, ?)", (user_id, series_id, datetime.now(timezone.utc).isoformat()))
            added = cursor.rowcount == 1
            await cursor.close()
            await db.commit()
            return added

    async def viewed_ids(self, user_id: int) -> set[int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT series_id FROM confirmed_views WHERE user_id=?", (user_id,)) as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def trend_days(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM catalog_settings WHERE key='trend_days'") as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 7

    async def set_trend_days(self, days: int) -> None:
        if type(days) is not int or not 1 <= days <= 365:
            raise ValueError("Trend period must be 1–365 days")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO catalog_settings VALUES ('trend_days', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (days,))
            await db.commit()

    async def view_counts(self) -> dict[int, tuple[int, int]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=await self.trend_days())).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT series_id, COUNT(*), SUM(confirmed_at >= ?) FROM confirmed_views GROUP BY series_id", (cutoff,)) as cursor:
                return {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}
