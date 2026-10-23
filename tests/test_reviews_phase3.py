import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio

from database.connection import init_db
from database.repository import UserRepository
from database.reviews_repository import ReviewsRepository, ReviewError
import database.reviews_repository as repository_module
from services.reviews_service import ReviewsService
from services.moderation_service import DEFAULT_WORDS, moderate_text
from tests.test_catalog_phase2 import make_catalog, URL


@pytest_asyncio.fixture
async def app(tmp_path):
    catalog = await make_catalog(tmp_path)
    catalog.series.add_series("Series", URL)
    users = UserRepository(catalog.repository.db_path)
    for user in (51, 52, 53, 99):
        await users.ensure_user(user, first_name=f"Person {user}", language="en")
        await users.set_captcha_passed(user, True)
    await users.set_admin(99, True)
    service = ReviewsService(catalog, ReviewsRepository(catalog.repository.db_path))
    return SimpleNamespace(service=service, repo=service.repository, catalog=catalog, users=users)


async def reset_limit(app):
    async with app.repo.connection(True) as db:
        await db.execute("DELETE FROM review_submission_limits")


@pytest.mark.asyncio
async def test_rating_upsert_average_constraints_concurrency_and_delete(app):
    await asyncio.gather(*[app.service.rate(51, 1, 7) for _ in range(8)])
    assert (await app.repo.rating_summary(1, 51)) == {"average": 7, "votes": 1, "own": 7}
    await app.service.rate(51, 1, 10)
    await app.service.rate(52, 1, 6)
    assert await app.repo.rating_summary(1, 51) == {"average": 8, "votes": 2, "own": 10}
    for score in (0, 11, True, 3.5, "5"):
        with pytest.raises(ReviewError):
            await app.service.rate(51, 1, score)
    await app.service.admin_action(99, "rating_delete", 51, "Invalid vote", "remove-rating", extra=1)
    assert await app.repo.rating_summary(1, 52) == {"average": 6, "votes": 1, "own": 6}


@pytest.mark.asyncio
async def test_top_requires_minimum_votes_and_ignores_hidden(app):
    app.catalog.series.add_series("Other", URL)
    await app.service.rate(51, 1, 10)
    await app.service.rate(52, 2, 8)
    await app.service.rate(53, 2, 9)
    assert (await app.catalog.page("top", 51)).items == []
    await app.repo.configure(99, {"min_votes": 2})
    assert [row["id"] for row in (await app.catalog.page("top", 51)).items] == [2]
    app.catalog.series.edit_series(2, publication_status="hidden")
    assert (await app.catalog.page("top", 51)).items == []
    with pytest.raises(ReviewError):
        await app.service.rate(51, 2, 9)


@pytest.mark.asyncio
async def test_review_pre_moderation_edit_version_soft_delete_and_restart(app):
    row = await app.service.submit(51, 1, "A thoughtful review & <tags>", "msg-1")
    assert row["status"] == "pending"
    assert (await app.repo.public_reviews(1))[0] == []
    await app.service.admin_action(99, "published", row["id"], "Approved", "approve-1", row["version"])
    assert (await app.repo.public_reviews(1))[0][0]["text"] == row["text"]
    await app.repo.configure(99, {"auto_safe": True})
    await reset_limit(app)
    edited = await app.service.submit(51, 1, "Changed and still safe review", "msg-2")
    assert edited["id"] == row["id"] and edited["version"] > row["version"]
    assert edited["status"] == "pending"
    assert (await app.repo.public_reviews(1))[0] == []
    with pytest.raises(ReviewError, match="stale"):
        await app.service.admin_action(99, "published", row["id"], "Old decision", "stale", row["version"])
    await app.repo.delete_own(51, edited["id"], edited["version"])
    await init_db(app.repo.db_path)
    assert (await ReviewsRepository(app.repo.db_path).review(row["id"]))["status"] == "deleted"
    with sqlite3.connect(app.repo.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM review_moderation_events WHERE review_id=?", (row["id"],)).fetchone()[0] >= 4


@pytest.mark.asyncio
async def test_submission_idempotency_concurrency_and_cooldown(app):
    rows = await asyncio.gather(*[app.service.submit(51, 1, "A good detailed review", "same-message") for _ in range(5)])
    assert {row["version"] for row in rows} == {1}
    with pytest.raises(ReviewError, match="cooldown"):
        await app.service.submit(51, 1, "A changed detailed review", "different-message")
    for text in ("short", "x" * 1001):
        with pytest.raises(ReviewError, match="length"):
            await app.service.submit(51, 1, text, "bad")


@pytest.mark.asyncio
async def test_moderation_modes_safe_suspicious_forbidden_and_editable_words(app):
    await app.repo.configure(99, {"auto_safe": True})
    row = await app.service.submit(51, 1, "Wonderful plot and acting!", "safe")
    assert row["status"] == "published"
    row = await app.service.submit(52, 1, "Visit https://example.com now", "link")
    assert row["status"] == "pending" and "link" in row["reasons"]
    await app.repo.configure(99, {"forbidden_action": "rejected"}, [*DEFAULT_WORDS, "forbidden phrase"])
    row = await app.service.submit(53, 1, "This forbidden phrase is here", "blocked-phrase")
    assert row["status"] == "rejected"
    await app.repo.configure(99, {}, [])
    await init_db(app.repo.db_path)
    assert await app.repo.words() == []  # restart must not restore removed words


@pytest.mark.parametrize("text", ["Это хуйня полная", "Ти йобаний дурень", "this is fucking bad", "This is sh1t", "Это xуй", "s.h.i.t", "х у й", "Ебaть", "f\u200buck", "f*ck", "sh!t", "х*й"])
def test_multilingual_profanity_and_simple_evasion(text):
    assert moderate_text(text, list(DEFAULT_WORDS)).level == "forbidden"


@pytest.mark.parametrize("text", ["A classic and classy story", "Scunthorpe is a town", "Нужна страховка для поездки", "Це чудовий серіал 💛", "A solid performance by the cast"])
def test_boundaries_prevent_false_positives(text):
    assert moderate_text(text, list(DEFAULT_WORDS)).level == "safe"


@pytest.mark.parametrize("text", ["aaaaaaaaaaaa", "word word word word word word", "Купить прямо сейчас", "Follow @advertiser", "www.example.com"])
def test_suspicious_spam_links_advertising(text):
    assert moderate_text(text, list(DEFAULT_WORDS)).level == "suspicious"


@pytest.mark.asyncio
async def test_reports_unique_no_self_report_and_dismissal(app):
    row = await app.service.submit(51, 1, "A good detailed review", "report-source")
    await app.service.admin_action(99, "published", row["id"], "OK", "publish", row["version"])
    with pytest.raises(ReviewError):
        await app.service.report(51, row["id"], "spam")
    await asyncio.gather(*[app.service.report(52, row["id"], "spam") for _ in range(5)])
    reports, _, _ = await app.repo.admin_page("reports")
    assert len(reports) == 1
    assert (await app.users.get_user_by_telegram_id(51)).is_blocked is False
    await app.service.admin_action(99, "dismiss", reports[0]["id"], "Unfounded", "dismiss")
    assert (await app.repo.admin_page("reports"))[0] == []
    assert (await app.repo.review(row["id"]))["status"] == "published"


@pytest.mark.asyncio
async def test_warnings_bans_unban_revoke_protection_and_permissions(app, monkeypatch):
    monkeypatch.setattr(repository_module, "settings", SimpleNamespace(ADMIN_IDS=(99,)))
    with pytest.raises(ReviewError, match="access"):
        await app.service.admin_action(51, "ban", 52, "Forged", "denied")
    with pytest.raises(ReviewError, match="protected"):
        await app.service.admin_action(99, "ban", 99, "Mistake", "primary")
    await app.service.admin_action(99, "warn", 51, "Be polite", "warning1")
    await app.service.admin_action(99, "warn", 51, "Be polite", "warning1")
    history = await app.repo.user_history(51)
    assert len(history["warnings"]) == 1
    assert history["is_blocked"] == 0
    await app.service.admin_action(99, "revoke", history["warnings"][0]["id"], "Resolved", "revoke1")
    assert (await app.repo.user_history(51))["warnings"][0]["revoked_at"]
    await app.repo.configure(99, {"auto_ban": True, "warning_threshold": 1})
    await app.service.admin_action(99, "warn", 51, "Repeated abuse", "warning2")
    assert (await app.users.get_user_by_telegram_id(51)).is_blocked is True
    with pytest.raises(ReviewError, match="access"):
        await app.service.rate(51, 1, 5)
    await app.service.admin_action(99, "unban", 51, "Appeal approved", "unban")
    assert (await app.users.get_user_by_telegram_id(51)).is_blocked is False
    assert (await app.repo.user_history(51))["bans"][0]["revoked_at"]


@pytest.mark.asyncio
async def test_review_ownership_and_sql_safety(app):
    row = await app.service.submit(51, 1, "Robert'); DROP TABLE users; --", "sql")
    with pytest.raises(ReviewError):
        await app.repo.delete_own(52, row["id"], row["version"])
    assert (await app.users.get_user_by_telegram_id(51)).telegram_id == 51
    assert len((await app.repo.admin_page("search", search="DROP TABLE"))[0]) == 1


@pytest.mark.asyncio
async def test_old_rating_delivery_cannot_revert_newer_rating(app):
    await app.service.rate(51, 1, 5, "old-callback")
    await app.service.rate(51, 1, 9, "new-callback")
    await app.service.rate(51, 1, 5, "old-callback")
    assert (await app.repo.rating_summary(1, 51))["own"] == 9


@pytest.mark.asyncio
async def test_phase3_schema_foreign_keys_indices_and_preservation(app):
    await app.service.rate(51, 1, 8)
    row = await app.service.submit(51, 1, "A preserved review after restart", "persist")
    await init_db(app.repo.db_path)
    assert (await app.repo.review(row["id"]))["text"] == row["text"]
    assert (await app.repo.rating_summary(1, 51))["own"] == 8
    async with app.repo.connection() as db:
        assert (await app.repo.one(db, "PRAGMA foreign_keys"))["foreign_keys"] == 1
        assert await app.repo.one(db, "PRAGMA foreign_key_check") is None
        assert await app.repo.one(db, "SELECT version FROM schema_migrations WHERE version=3")
        plan = await app.repo.one(db, "EXPLAIN QUERY PLAN SELECT * FROM reviews WHERE series_id=1 AND status='published' ORDER BY published_at,id")
        assert "reviews_public" in plan["detail"]


@pytest.mark.asyncio
async def test_dictionary_concurrent_changes_and_unicode_search(app):
    await asyncio.gather(app.repo.change_word(99, "custom one", True), app.repo.change_word(99, "custom two", True))
    assert {"custom one", "custom two"}.issubset(set(await app.repo.words()))
    await app.service.submit(51, 1, "Прекрасная История и актёры", "unicode")
    assert len((await app.repo.admin_page("search", search="ИСТОРИЯ"))[0]) == 1
