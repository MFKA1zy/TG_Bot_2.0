"""Additive phase-three schema. Series remain in the existing JSON catalog."""
import aiosqlite


async def create_reviews_tables(db: aiosqlite.Connection) -> None:
    async with db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'") as cursor:
        installed = await cursor.fetchone() is not None
    if installed:
        async with db.execute("SELECT 1 FROM schema_migrations WHERE version=3") as cursor:
            installed = await cursor.fetchone() is not None
    await db.executescript("""
    CREATE TABLE IF NOT EXISTS ratings (
        user_id INTEGER NOT NULL REFERENCES users(telegram_id),
        series_id INTEGER NOT NULL,
        score INTEGER NOT NULL CHECK(typeof(score)='integer' AND score BETWEEN 1 AND 10),
        updated_at TEXT NOT NULL, PRIMARY KEY(user_id,series_id)
    );
    CREATE INDEX IF NOT EXISTS ratings_series ON ratings(series_id,score);
    CREATE TABLE IF NOT EXISTS rating_requests (
        request_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(telegram_id),
        series_id INTEGER NOT NULL,score INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(telegram_id), series_id INTEGER NOT NULL,
        text TEXT NOT NULL CHECK(length(text) BETWEEN 10 AND 1000),
        status TEXT NOT NULL CHECK(status IN ('pending','published','rejected','deleted')),
        reasons TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT,
        UNIQUE(user_id,series_id)
    );
    CREATE INDEX IF NOT EXISTS reviews_queue ON reviews(status,updated_at,id);
    CREATE INDEX IF NOT EXISTS reviews_public ON reviews(series_id,status,published_at,id);
    CREATE TABLE IF NOT EXISTS review_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        review_id INTEGER NOT NULL REFERENCES reviews(id),
        user_id INTEGER NOT NULL REFERENCES users(telegram_id),
        reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','dismissed','resolved')),
        created_at TEXT NOT NULL, UNIQUE(review_id,user_id)
    );
    CREATE INDEX IF NOT EXISTS reports_queue ON review_reports(status,id);
    CREATE TABLE IF NOT EXISTS review_moderation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER REFERENCES users(telegram_id),
        subject_id INTEGER REFERENCES users(telegram_id), review_id INTEGER REFERENCES reviews(id),
        action TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL, request_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS moderation_user ON review_moderation_events(subject_id,id);
    CREATE TABLE IF NOT EXISTS user_warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(telegram_id),
        actor_id INTEGER NOT NULL REFERENCES users(telegram_id), reason TEXT NOT NULL,
        created_at TEXT NOT NULL, revoked_at TEXT
    );
    CREATE INDEX IF NOT EXISTS warnings_user ON user_warnings(user_id,revoked_at,id);
    CREATE TABLE IF NOT EXISTS user_bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(telegram_id),
        actor_id INTEGER NOT NULL REFERENCES users(telegram_id), reason TEXT NOT NULL,
        created_at TEXT NOT NULL, revoked_at TEXT
    );
    CREATE INDEX IF NOT EXISTS bans_user ON user_bans(user_id,revoked_at,id);
    CREATE TABLE IF NOT EXISTS review_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS moderation_words (phrase TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS review_submission_limits (
        user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id), submitted_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS review_submissions (
        request_key TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(telegram_id),
        series_id INTEGER NOT NULL, review_id INTEGER NOT NULL REFERENCES reviews(id)
    );
    CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    """)
    if not installed:
        from services.moderation_service import DEFAULT_WORDS

        await db.executemany("INSERT OR IGNORE INTO moderation_words VALUES (?)", [(word,) for word in DEFAULT_WORDS])
    await db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (3, strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))")
