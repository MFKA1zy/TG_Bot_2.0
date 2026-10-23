import sqlite3

import pytest

import config
from database.connection import init_db
from database.repository import UserRepository


def test_local_legacy_database_path_is_preserved(tmp_path, monkeypatch):
    (tmp_path / "database").mkdir()
    legacy = tmp_path / "database" / "users.db"
    legacy.touch()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert config._default_db_path(tmp_path / "data") == legacy
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "volume"))
    assert config._default_db_path(tmp_path / "volume") == tmp_path / "volume" / "users.db"


@pytest.mark.asyncio
async def test_user_column_migration_backs_up_original_and_survives_restart(tmp_path):
    path = tmp_path / "users.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE,
            username TEXT, first_name TEXT, last_name TEXT,
            is_admin INTEGER DEFAULT 0, captcha_passed INTEGER DEFAULT 0,
            created_at TEXT, last_activity TEXT, is_blocked INTEGER DEFAULT 0
        )""")
        db.execute("INSERT INTO users (telegram_id, username, captcha_passed) VALUES (51, 'original', 1)")
    await init_db(path)
    backups = list(tmp_path.glob("users.db.pre-migration.*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert "language" not in {row[1] for row in db.execute("PRAGMA table_info(users)")}
        assert db.execute("SELECT username FROM users").fetchone() == ("original",)
    await init_db(path)
    user = await UserRepository(str(path)).get_user_by_telegram_id(51)
    assert user.username == "original"
    assert user.captcha_passed is True
    assert len(list(tmp_path.glob("users.db.pre-migration.*.bak"))) == 1
