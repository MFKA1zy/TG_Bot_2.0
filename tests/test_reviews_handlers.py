import pytest
from aiogram.fsm.storage.base import StorageKey

from tests.test_catalog_handlers import app
from tests.test_catalog_phase2 import URL
from services.i18n import tr
import handlers.reviews as reviews_ui
import handlers.admin.reviews as moderation_ui


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['ru','uk','en'])
async def test_one_click_approval_is_audited_idempotent_and_checks_role(app,language):
    await app.users.set_language(51,language)
    app.catalog.series.add_series('Series',URL)
    row=await reviews_ui.service.submit(51,1,'A detailed review to approve','approve-once')
    action=f"mod:act:published:{row['id']}:{row['version']}:0"
    await app.callback(action)
    assert (await reviews_ui.service.repository.review(row['id']))['status']=='pending'
    await app.users.set_admin(51,True)
    await app.callback(action)
    published=await reviews_ui.service.repository.review(row['id'])
    assert published['status']=='published' and published['version']==row['version']+1
    buttons=[button for group in app.session.calls[-1].reply_markup.inline_keyboard for button in group]
    assert not any((button.callback_data or '').startswith('mod:act:published:') for button in buttons)
    assert not any(button.callback_data=='mod:commit' for button in buttons)
    await app.callback(action)
    assert (await reviews_ui.service.repository.review(row['id']))['version']==published['version']
    async with reviews_ui.service.repository.connection() as db:
        async with db.execute("SELECT * FROM review_moderation_events WHERE review_id=? AND action='published'",(row['id'],)) as cursor:
            events=await cursor.fetchall()
    assert len(events)==1 and events[0]['reason']==tr(language,'rv_approval_reason')
    assert await app.dp.storage.get_state(StorageKey(bot_id=app.bot.id,chat_id=51,user_id=51)) is None


@pytest.mark.asyncio
async def test_rating_review_fsm_own_delete_and_return_navigation(app):
    app.catalog.series.add_series("Series", URL)
    await app.callback("rev:rate:1:all:0:0")
    values = [button.text for row in app.session.calls[-1].reply_markup.inline_keyboard for button in row]
    assert all(str(value) in values for value in range(1, 11))
    await app.callback("rev:score:1:9:all:0:0")
    assert "Your rating: 9/10" in app.session.calls[-1].text
    await app.callback("rev:write:1:all:0:0")
    await app.text("A useful review with <b>markup</b>")
    assert "moderation" in app.session.calls[-1].text
    row = await reviews_ui.service.repository.own_review(51, 1)
    assert row["status"] == "pending"
    await app.callback("rev:own:1:all:0:0")
    assert "&lt;b&gt;" in app.session.calls[-1].text
    await app.callback(f"rev:delete:{row['id']}:{row['version']}:1:all:0:0")
    assert (await reviews_ui.service.repository.review(row["id"]))["status"] == "pending"
    await app.callback(f"rev:delok:{row['id']}:{row['version']}:1:all:0:0")
    assert (await reviews_ui.service.repository.review(row["id"]))["status"] == "deleted"
    await app.callback("rev:back:1:all:0:0")
    assert "Rating:" in app.session.calls[-1].text


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["captcha", "subscription", "blocked"])
async def test_review_fsm_and_rating_recheck_access(app, reason):
    app.catalog.series.add_series("Series", URL)
    await app.callback("rev:write:1:all:0:0")
    if reason == "captcha":
        await app.users.set_captcha_passed(51, False)
    elif reason == "subscription":
        app.subscription.return_value = False
    else:
        await app.users.set_user_blocked(51, True)
    await app.text("This must never be submitted")
    await app.callback("rev:score:1:10:all:0:0")
    assert await reviews_ui.service.repository.own_review(51, 1) is None
    assert (await reviews_ui.service.repository.rating_summary(1, 51))["votes"] == 0


@pytest.mark.asyncio
async def test_public_reviews_reports_no_public_telegram_id_and_admin_moderation(app):
    app.catalog.series.add_series("Series", URL)
    await app.users.ensure_user(52, first_name="Other author", language="en")
    await app.users.set_admin(51, True)
    row = await reviews_ui.service.submit(52, 1, "Thoughtful public review <tag>", "author52")
    await reviews_ui.service.admin_action(51, "published", row["id"], "Approved", "approve52", row["version"])
    await app.callback("rev:list:1:0:all:0:0")
    text = app.session.calls[-1].text
    assert "Other author" in text and "&lt;tag&gt;" in text and "ID " not in text
    await app.callback(f"rev:reason:{row['id']}:spam:1:all:0:0")
    assert "saved" in app.session.calls[-1].text
    await app.callback("mod:queue:reports:0")
    assert any("mod:report:" in (button.callback_data or "") for group in app.session.calls[-1].reply_markup.inline_keyboard for button in group)
    await app.callback(f"mod:review:{row['id']}")
    assert "ID 52" in app.session.calls[-1].text
    current = await reviews_ui.service.repository.review(row["id"])
    await app.callback(f"mod:act:rejected:{row['id']}:{current['version']}:0")
    await app.text("Spam after manual inspection")
    await app.callback("mod:commit", message_id=len(app.session.calls) + 100)
    assert (await reviews_ui.service.repository.review(row["id"]))["status"] == "rejected"
    assert (await reviews_ui.service.repository.admin_page("reports"))[0] == []


@pytest.mark.asyncio
async def test_moderator_permission_stale_confirmation_and_version_check(app):
    app.catalog.series.add_series("Series", URL)
    row = await reviews_ui.service.submit(51, 1, "A detailed initial review", "first")
    await app.callback("mod:home")
    assert "administrator panel" in app.session.calls[-1].text
    await app.users.set_admin(51, True)
    async with reviews_ui.service.repository.connection(True) as db:
        await db.execute("DELETE FROM review_submission_limits")
    await reviews_ui.service.submit(51, 1, "A completely changed review", "second")
    await app.callback(f"mod:act:published:{row['id']}:{row['version']}:0")
    assert "data changed" in app.session.calls[-1].text
    assert (await reviews_ui.service.repository.review(row["id"]))["status"] == "pending"


@pytest.mark.asyncio
async def test_hidden_series_cannot_be_rated_reviewed_or_read_via_stale_buttons(app):
    app.catalog.series.add_series("Hidden", URL, publication_status="hidden")
    for data in ("rev:score:1:8:all:0:0", "rev:write:1:all:0:0", "rev:list:1:0:all:0:0"):
        await app.callback(data)
        assert "unavailable" in app.session.calls[-1].text
    assert (await reviews_ui.service.repository.rating_summary(1, 51))["votes"] == 0


@pytest.mark.asyncio
async def test_callback_ack_precedes_subscription_and_write(app):
    app.catalog.series.add_series("Series", URL)
    async def subscription(bot, user_id):
        assert app.session.calls[-1].__api_method__ == "answerCallbackQuery"
        return True
    app.subscription.side_effect = subscription
    await app.callback("rev:score:1:7:all:0:0")
    assert (await reviews_ui.service.repository.rating_summary(1, 51))["own"] == 7


@pytest.mark.asyncio
async def test_navigation_cancels_stale_review_input(app):
    app.catalog.series.add_series("Series", URL)
    await app.callback("rev:write:1:all:0:0")
    await app.callback("rev:own:1:all:0:0")
    await app.text("Should not be submitted after leaving editor")
    assert await reviews_ui.service.repository.own_review(51, 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("language,expected", [("ru", "модерацию"), ("uk", "модерацію"), ("en", "moderation")])
async def test_submission_uses_saved_language(app, language, expected):
    app.catalog.series.add_series("Series", URL)
    await app.users.set_language(51, language)
    await app.callback("rev:write:1:all:0:0")
    await app.text("A helpful review in any locale")
    assert expected in app.session.calls[-1].text


@pytest.mark.asyncio
async def test_existing_telegram_poster_file_id_is_used_first(app):
    app.catalog.series.add_series("Series", URL, photo_file_id="already-on-telegram", photo_url="https://i.ibb.co/a/photo.jpg")
    await app.callback("cat:card:1:all:0:0")
    photos = [method for method in app.session.calls if method.__api_method__ == "sendPhoto"]
    assert photos[0].photo == "already-on-telegram"
