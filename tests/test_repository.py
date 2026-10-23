import asyncio

import pytest

from database.connection import init_db
from database.repository import UserRepository


@pytest.mark.asyncio
async def test_user_registration_updates_profile_without_resetting_flags(tmp_path):
    db_path = tmp_path / "users.db"
    await init_db(db_path)
    repository = UserRepository(str(db_path))

    first = await repository.ensure_user(42, "old_name", "First", "User")
    assert first.telegram_id == 42
    assert first.captcha_passed is False
    await repository.set_captcha_passed(42, True)
    await repository.set_admin(42, True)

    updated = await repository.ensure_user(42, "new_name", "Updated", None)
    fetched = await repository.get_user_by_telegram_id(42)

    assert updated.username == "new_name"
    assert updated.first_name == "Updated"
    assert updated.is_admin is True
    assert updated.captcha_passed is True
    assert fetched == updated


@pytest.mark.asyncio
async def test_user_language_is_saved_and_not_reset_by_profile_updates(tmp_path):
    db_path = tmp_path / "users.db"
    await init_db(db_path)
    repository = UserRepository(str(db_path))

    first = await repository.ensure_user(42, language="uk")
    await repository.ensure_user(42, username="updated")
    await repository.set_language(42, "en")
    fetched = await repository.get_user_by_telegram_id(42)

    assert first.language == "uk"
    assert fetched is not None
    assert fetched.language == "en"


@pytest.mark.asyncio
async def test_repository_captcha_state_stats_and_broadcast_history(tmp_path):
    db_path = tmp_path / "users.db"
    await init_db(db_path)
    repository = UserRepository(str(db_path))

    await repository.ensure_user(100, username="allowed")
    await repository.ensure_user(200, username="blocked")
    await repository.set_captcha_passed(100, True)
    await repository.set_admin(100, True)
    await repository.set_user_blocked(200, True)
    await repository.update_captcha_state(100, 2)

    captcha_state = await repository.get_captcha_state(100)
    assert captcha_state["attempts"] == 2
    assert await repository.get_active_user_ids() == [100]

    run_id = await repository.start_broadcast_history("hello", 1)
    assert await repository.has_unfinished_broadcast() is True
    await repository.finish_broadcast_history(
        run_id,
        success_count=1,
        failed_count=0,
        processed_count=1,
        cancelled=False,
    )
    assert await repository.has_unfinished_broadcast() is False

    stats = await repository.get_stats()
    assert stats["total_users"] == 2
    assert stats["captcha_passed"] == 1
    assert stats["admins"] == 1
    assert stats["blocked_users"] == 1
    assert stats["new_today"] == 2
    assert stats["new_last_7_days"] == 2
    assert stats["broadcasts"] == 1


@pytest.mark.asyncio
async def test_attempt_counters_are_atomic_for_parallel_updates(tmp_path):
    db_path = tmp_path / "users.db"
    await init_db(db_path)
    repository = UserRepository(str(db_path))

    captcha_attempts = await asyncio.gather(
        *[repository.increment_captcha_attempts(12) for _ in range(5)]
    )
    activation_attempts = await asyncio.gather(
        *[repository.increment_admin_activation_attempts(12) for _ in range(5)]
    )

    assert sorted(captcha_attempts) == [1, 2, 3, 4, 5]
    assert sorted(activation_attempts) == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_init_db_migrates_existing_broadcast_history_without_data_loss(tmp_path):
    db_path = tmp_path / "legacy.db"
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE broadcast_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                text TEXT,
                target_count INTEGER,
                success_count INTEGER,
                failed_count INTEGER
            )
            """
        )
        await db.execute(
            "INSERT INTO broadcast_history (text, target_count) VALUES ('old', 3)"
        )
        await db.commit()

    await init_db(db_path)
    repository = UserRepository(str(db_path))
    stats = await repository.get_stats()

    assert stats["broadcasts"] == 1
    assert await repository.has_unfinished_broadcast() is False
    backups = list(tmp_path.glob("legacy.db.pre-migration.*.bak"))
    assert len(backups) == 1
    async with aiosqlite.connect(backups[0]) as backup_db:
        cursor = await backup_db.execute(
            "SELECT text, target_count FROM broadcast_history"
        )
        assert await cursor.fetchone() == ("old", 3)
        await cursor.close()
