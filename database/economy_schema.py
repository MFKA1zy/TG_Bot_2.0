import aiosqlite


async def create_economy_tables(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    for column in ("points_balance", "points_earned", "points_spent"):
        if column not in columns:
            await db.execute(f"ALTER TABLE users ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0 CHECK({column}>=0 AND {column}<=9000000000000000)")
    async with db.execute("PRAGMA table_info(confirmed_views)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if "reward_eligible" not in columns:
        await db.execute("ALTER TABLE confirmed_views ADD COLUMN reward_eligible INTEGER NOT NULL DEFAULT 0 CHECK(reward_eligible IN (0,1))")
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS points_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(telegram_id),
            operation TEXT NOT NULL CHECK(operation IN ('view','admin_credit','admin_debit','roulette','adjustment')),
            amount INTEGER NOT NULL CHECK(typeof(amount)='integer'),
            reason TEXT NOT NULL, series_id INTEGER, series_title TEXT,
            actor_id INTEGER REFERENCES users(telegram_id),
            created_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
            balance_after INTEGER NOT NULL CHECK(balance_after>=0)
        );
        CREATE INDEX IF NOT EXISTS ledger_user ON points_ledger(user_id,id DESC);
        CREATE INDEX IF NOT EXISTS ledger_created ON points_ledger(created_at,user_id);
        CREATE UNIQUE INDEX IF NOT EXISTS ledger_unique_view ON points_ledger(user_id,series_id) WHERE operation='view';
        CREATE INDEX IF NOT EXISTS leaderboard_earned ON users(is_blocked,points_earned DESC,telegram_id);
        CREATE INDEX IF NOT EXISTS leaderboard_balance ON users(is_blocked,points_balance DESC,telegram_id);
        CREATE INDEX IF NOT EXISTS reviews_user_status ON reviews(user_id,status);
        CREATE INDEX IF NOT EXISTS views_legacy ON confirmed_views(reward_eligible,user_id,series_id);
        CREATE TABLE IF NOT EXISTS economy_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS economy_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL REFERENCES users(telegram_id),
            action TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL
        );
    """)
    await db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (4,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))")
