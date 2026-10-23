import aiosqlite

DEFAULT_PRIZES=((0,20),(5,30),(10,25),(20,15),(50,8),(100,2))


async def create_roulette_tables(db:aiosqlite.Connection):
    async with db.execute("SELECT 1 FROM schema_migrations WHERE version=5") as cursor:
        if await cursor.fetchone():
            return
    await db.execute("BEGIN IMMEDIATE")
    try:
        # SQLite cannot extend an existing CHECK in place. Preserve every row/ID
        # while replacing only the ledger's physical schema, in one transaction.
        async with db.execute("SELECT sql FROM sqlite_master WHERE name='points_ledger'") as cursor:
            sql=(await cursor.fetchone())[0]
        if "'roulette_reward'" not in sql:
            async with db.execute("SELECT seq FROM sqlite_sequence WHERE name='points_ledger'") as cursor:
                sequence=await cursor.fetchone()
            async with db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='points_ledger' AND type IN ('index','trigger') AND sql IS NOT NULL") as cursor:
                dependent_sql=[row[0] for row in await cursor.fetchall()]
            await db.execute(sql.replace("CREATE TABLE points_ledger", "CREATE TABLE points_ledger_v5").replace("CREATE TABLE IF NOT EXISTS points_ledger", "CREATE TABLE points_ledger_v5").replace("'roulette'", "'roulette','roulette_reward'"))
            await db.execute("INSERT INTO points_ledger_v5 SELECT * FROM points_ledger")
            await db.execute("DROP TABLE points_ledger")
            await db.execute("ALTER TABLE points_ledger_v5 RENAME TO points_ledger")
            for statement in dependent_sql:
                await db.execute(statement)
            if sequence:
                await db.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='points_ledger'",(sequence[0],))
        statements=[
            """CREATE TABLE roulette_settings(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),cooldown_seconds INTEGER NOT NULL CHECK(cooldown_seconds BETWEEN 3600 AND 2592000),version INTEGER NOT NULL)""",
            """CREATE TABLE roulette_prizes(id INTEGER PRIMARY KEY AUTOINCREMENT,points INTEGER NOT NULL CHECK(points BETWEEN 0 AND 1000000),weight INTEGER NOT NULL CHECK(weight BETWEEN 0 AND 100),enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
            """CREATE TABLE roulette_spins(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(telegram_id),prize_id INTEGER NOT NULL REFERENCES roulette_prizes(id),awarded_points INTEGER NOT NULL CHECK(awarded_points>=0),spun_at TEXT NOT NULL,next_available_at TEXT NOT NULL,transaction_id INTEGER NOT NULL UNIQUE REFERENCES points_ledger(id),request_key TEXT NOT NULL UNIQUE)""",
            "CREATE INDEX roulette_user_history ON roulette_spins(user_id,id DESC)",
            "CREATE INDEX roulette_prize_history ON roulette_spins(prize_id,awarded_points)",
        ]
        for statement in statements:
            await db.execute(statement)
        await db.execute("INSERT INTO roulette_settings VALUES (1,1,86400,1)")
        await db.executemany("INSERT INTO roulette_prizes(points,weight,enabled,created_at,updated_at) VALUES (?,?,1,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))",DEFAULT_PRIZES)
        async with db.execute("PRAGMA foreign_key_check") as cursor:
            if await cursor.fetchone():
                raise RuntimeError("Foreign key check failed during roulette migration")
        await db.execute("INSERT INTO schema_migrations VALUES (5,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))")
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
