from datetime import datetime, timezone
import html
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiogram.filters import CommandStart
import handlers.start as start_ui
import handlers.captcha as captcha_ui
from states.captcha import CaptchaStates

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import handlers.catalog as ui
import handlers.admin.catalog as admin_ui
import handlers.admin.panel as admin_panel
import handlers.inline_search as inline
import handlers.reviews as reviews_ui
import handlers.admin.reviews as moderation_ui
import handlers.profile as profile_ui
import handlers.admin.economy as economy_ui
import handlers.roulette as roulette_ui
import handlers.admin.roulette as roulette_admin_ui
from database.roulette_repository import RouletteRepository
from database.economy_repository import EconomyRepository
from services.reviews_service import ReviewsService
import database.repository as user_repository_module
from database.repository import UserRepository
from services.access_middleware import BlockedUserMiddleware
from services.i18n import LocaleMiddleware, tr
from services.message_service import message_service
from services.series_cards import render_card, show_card
from tests.test_catalog_phase2 import make_catalog, URL


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.broken_photo = False

    async def close(self):
        pass

    async def stream_content(self, url, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = method.__api_method__
        if name == "sendPhoto" and self.broken_photo:
            raise TelegramBadRequest(method=method, message="invalid photo URL")
        if name in {"answerCallbackQuery", "answerInlineQuery", "deleteMessage"}:
            return True
        if name == "getMe":
            return User(id=12345, is_bot=True, first_name="Bot", username="test_bot")
        return Message(message_id=getattr(method, "message_id", None) or len(self.calls) + 100,
                       date=datetime.now(timezone.utc), chat=Chat(id=51, type="private"),
                       text=getattr(method, "text", None), caption=getattr(method, "caption", None))


@pytest_asyncio.fixture
async def app(tmp_path, monkeypatch):
    catalog = await make_catalog(tmp_path)
    users = UserRepository(catalog.repository.db_path)
    await users.ensure_user(51, first_name="User", language="en")
    await users.set_captcha_passed(51, True)
    monkeypatch.setattr(user_repository_module, "settings", SimpleNamespace(DB_PATH=users.db_path))
    monkeypatch.setattr(ui, "catalog", catalog)
    monkeypatch.setattr(ui, "users", users)
    monkeypatch.setattr(admin_ui, "catalog", catalog)
    monkeypatch.setattr(admin_panel, "repository", users)
    monkeypatch.setattr(start_ui,"repository",users)
    monkeypatch.setattr(captcha_ui,"repository",users)
    review_service = ReviewsService(catalog)
    monkeypatch.setattr(reviews_ui, "service", review_service)
    monkeypatch.setattr(moderation_ui, "service", review_service)
    points=EconomyRepository(catalog.repository.db_path)
    monkeypatch.setattr(profile_ui,"economy",points)
    monkeypatch.setattr(profile_ui,"catalog",catalog)
    monkeypatch.setattr(economy_ui,"economy",points)
    roulette=RouletteRepository(users.db_path)
    monkeypatch.setattr(roulette_ui,"roulette",roulette)
    monkeypatch.setattr(roulette_admin_ui,"roulette",roulette)
    subscription = AsyncMock(return_value=True)
    monkeypatch.setattr(ui, "has_active_subscription", subscription)
    monkeypatch.setattr("services.access_service.has_active_subscription", subscription)
    monkeypatch.setattr(message_service, "track_message", lambda *args: None)
    monkeypatch.setattr(message_service, "track_user_message", lambda *args: None)
    monkeypatch.setattr(message_service, "clear_chat", AsyncMock())
    monkeypatch.setattr(message_service, "safe_delete", AsyncMock())
    session = TelegramSession()
    bot = Bot("12345:TEST", session=session)
    dp = Dispatcher()
    dp.update.outer_middleware(LocaleMiddleware())
    dp.update.outer_middleware(BlockedUserMiddleware())
    router = Router()
    router.message.register(start_ui.command_start,CommandStart())
    router.callback_query.register(start_ui.set_language,F.data.startswith('set_language:'))
    router.callback_query.register(start_ui.check_subscription,F.data=='check_subscription')
    router.callback_query.register(start_ui.go_to_main_menu,F.data=='main_menu')
    router.message.register(captcha_ui.process_captcha,CaptchaStates.waiting_for_answer)
    router.callback_query.register(ui.catalog_callback, F.data.startswith("cat:"))
    router.callback_query.register(admin_ui.admin_catalog_callback, F.data.startswith("acat:"))
    router.callback_query.register(reviews_ui.review_callback, F.data.startswith("rev:"))
    router.callback_query.register(moderation_ui.moderation_callback, F.data.startswith("mod:"))
    router.callback_query.register(profile_ui.profile_callback,F.data.startswith("pf:"))
    router.callback_query.register(economy_ui.economy_callback,F.data.startswith("ec:"))
    router.callback_query.register(roulette_ui.roulette_callback,F.data.startswith("rr:"))
    router.callback_query.register(roulette_admin_ui.admin_roulette_callback,F.data.startswith("ra:"))
    router.message.register(admin_ui.admin_catalog_input, admin_ui.CatalogAdminStates.input)
    router.message.register(reviews_ui.review_text, reviews_ui.ReviewStates.text)
    router.message.register(moderation_ui.moderation_input, moderation_ui.ModerationStates.input)
    router.message.register(economy_ui.economy_input,economy_ui.EconomyStates.input)
    router.message.register(roulette_admin_ui.admin_roulette_input,roulette_admin_ui.RouletteAdminStates.input)
    dp.include_router(router)
    counter = 0

    async def callback(data, message_id=10):
        nonlocal counter
        counter += 1
        sender = User(id=51, is_bot=False, first_name="User", language_code="en")
        message = Message(message_id=message_id, date=datetime.now(timezone.utc), chat=Chat(id=51, type="private"), text="old")
        update = Update(update_id=counter, callback_query=CallbackQuery(id=str(counter), chat_instance="chat", from_user=sender, data=data, message=message))
        await dp.feed_update(bot, update)

    async def text(value):
        nonlocal counter
        counter += 1
        sender = User(id=51, is_bot=False, first_name="User", language_code="en")
        message = Message(message_id=counter + 10, date=datetime.now(timezone.utc), chat=Chat(id=51, type="private"), from_user=sender, text=value)
        await dp.feed_update(bot, Update(update_id=counter, message=message))

    yield SimpleNamespace(catalog=catalog, users=users, session=session, callback=callback, text=text, subscription=subscription, bot=bot, dp=dp)
    await dp.storage.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_dispatcher_catalog_card_favorite_view_episode_navigation(app):
    category = await app.catalog.repository.save_category("Comedy")
    item = app.catalog.series.add_series("Series <b>", URL, category_ids=[category], description="Plot & details", episodes=[{"title": f"Ep {i}", "url": URL} for i in range(12)])
    await app.callback("cat:home")
    await app.callback("cat:list:all:0:0")
    await app.callback(f"cat:card:{item['id']}:all:0:0")
    assert "&lt;b&gt;" in app.session.calls[-1].text
    assert "Comedy" in app.session.calls[-1].text
    await app.callback(f"cat:fav:{item['id']}:1:all:0:0")
    await app.callback(f"cat:fav:{item['id']}:1:all:0:0")
    assert await app.catalog.repository.favorite_ids(51) == {item["id"]}
    await app.callback(f"cat:view:{item['id']}:all:0:0")
    assert "cannot verify TikTok" in app.session.calls[-1].text
    assert await app.catalog.repository.viewed_ids(51) == set()
    await app.callback(f"cat:confirm:{item['id']}:all:0:0")
    await app.callback(f"cat:confirm:{item['id']}:all:0:0")
    assert await app.catalog.repository.view_counts() == {item["id"]: (1, 1)}
    await app.callback(f"cat:watch:{item['id']}:1:all:0:0")
    buttons = [button for row in app.session.calls[-1].reply_markup.inline_keyboard for button in row]
    assert any(button.text == "Ep 11" for button in buttons)
    assert not any(button.text == "Ep 0" for button in buttons)
    assert all(len((button.callback_data or "").encode()) <= 64 for button in buttons)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["captcha", "blocked", "subscription"])
async def test_protected_catalog_callbacks_cannot_mutate_without_access(app, reason):
    item = app.catalog.series.add_series("Series", URL)
    if reason == "captcha":
        await app.users.set_captcha_passed(51, False)
    elif reason == "blocked":
        await app.users.set_user_blocked(51, True)
    else:
        app.subscription.return_value = False
    await app.callback(f"cat:fav:{item['id']}:1:all:0:0")
    await app.callback(f"cat:confirm:{item['id']}:all:0:0")
    assert await app.catalog.repository.favorite_ids(51) == set()
    assert await app.catalog.repository.viewed_ids(51) == set()


@pytest.mark.asyncio
async def test_hidden_and_deleted_series_stale_callbacks_are_safe(app):
    item = app.catalog.series.add_series("Series", URL)
    app.catalog.series.edit_series(item["id"], publication_status="hidden")
    await app.callback(f"cat:fav:{item['id']}:1:all:0:0")
    await app.callback(f"cat:confirm:{item['id']}:all:0:0")
    assert await app.catalog.repository.favorite_ids(51) == set()
    assert await app.catalog.repository.viewed_ids(51) == set()
    app.catalog.series.delete_series(item["id"])
    await app.callback(f"cat:card:{item['id']}:all:999:0")
    assert "No series" in app.session.calls[-1].text
    await app.callback("cat:card:invalid")
    assert "unavailable" in app.session.calls[-1].text


@pytest.mark.asyncio
async def test_admin_fsm_category_metadata_trend_and_permissions(app):
    await app.callback("acat:addcategory")
    assert app.session.calls[-1].text == tr("en", "admin_access_denied")
    await app.users.set_admin(51, True)
    await app.callback("acat:addcategory")
    await app.text("Comedy | 10")
    assert await app.catalog.repository.categories() == []
    await app.callback("acat:save", message_id=len(app.session.calls) + 100)
    assert (await app.catalog.repository.categories())[0]["name"] == "Comedy"
    item = app.catalog.series.add_series("Series", URL)
    await app.callback(f"acat:field:{item['id']}:description")
    await app.text("New plot")
    assert app.catalog.series.get_series_by_id(item["id"])["description"] == ""
    await app.callback("acat:save", message_id=len(app.session.calls) + 100)
    assert app.catalog.series.get_series_by_id(item["id"])["description"] == "New plot"
    await app.callback("acat:trend")
    await app.text("14")
    await app.callback("acat:save", message_id=len(app.session.calls) + 100)
    assert await app.catalog.repository.trend_days() == 14


@pytest.mark.asyncio
async def test_admin_stale_confirmation_and_invalid_inputs_do_not_write(app):
    await app.users.set_admin(51, True)
    await app.callback("acat:addcategory")
    await app.text("Bad format")
    assert await app.catalog.repository.categories() == []
    await app.text("Comedy | 1")
    await app.callback("acat:save", message_id=99999)
    assert await app.catalog.repository.categories() == []
    await app.callback("acat:trend")
    await app.text("0")
    assert await app.catalog.repository.trend_days() == 7


@pytest.mark.asyncio
async def test_broken_poster_falls_back_to_text(app):
    item = app.catalog.series.add_series("Poster", URL, photo_url="https://i.ibb.co/a/photo.jpg")
    app.session.broken_photo = True
    await app.callback(f"cat:card:{item['id']}:all:0:0")
    assert app.session.calls[-2].__api_method__ == "sendPhoto"
    assert app.session.calls[-1].__api_method__ == "editMessageText"
    assert "Poster" in app.session.calls[-1].text


@pytest.mark.parametrize("language", ["ru", "uk", "en"])
def test_cards_are_localized_and_escape_html(language):
    item = {"title": "<script>", "description": "A&B", "episode_count": 12, "release_status": "completed", "category_ids": [1]}
    text = render_card(item, [{"id": 1, "name": "<Genre>"}], language)
    assert "&lt;script&gt;" in text and "&lt;Genre&gt;" in text and "A&amp;B" in text
    assert "12" in text
    assert len(text) < 1024


@pytest.mark.parametrize("language", ["ru", "uk", "en"])
def test_maximum_length_unicode_card_fits_telegram_caption(language):
    item = {"title": "🎬" * 256, "description": "💛" * 500, "episode_count": 100000, "release_status": "completed", "category_ids": [1]}
    text = render_card(item, [{"id": 1, "name": "✨" * 60}], language, 999999)
    plain = html.unescape(re.sub(r"</?b>", "", text))
    assert len(plain.encode("utf-16-le")) // 2 <= 1024


@pytest.mark.parametrize("field,text,expected", [
    ("alternative_titles", "A, B", ["A", "B"]),
    ("alternative_titles", "-", []),
    ("category_ids", "1,2", [1, 2]),
    ("episode_count", "12", 12),
    ("description", "-", ""),
    ("recommended", "1", True),
    ("recommended", "0", False),
    ("episodes", "Ep 1 | https://example.com/1", [{"title": "Ep 1", "url": "https://example.com/1"}]),
])
def test_admin_field_input_formats(field, text, expected):
    assert admin_ui.parse_field(field, text) == expected


@pytest.mark.asyncio
async def test_inline_pagination_hides_unpublished_and_preserves_search(app, monkeypatch):
    for i in range(55):
        app.catalog.series.add_series(f"Series {i}", URL, alternative_titles=[f"Alias {i}"])
    app.catalog.series.add_series("Hidden", URL, publication_status="hidden")
    monkeypatch.setattr(inline, "series_service", app.catalog.series)
    monkeypatch.setattr(inline, "repository", app.users)
    monkeypatch.setattr(inline, "settings", SimpleNamespace(SUBSCRIPTION_REQUIRED=False))
    query = SimpleNamespace(from_user=SimpleNamespace(id=51, language_code="en"), query="", offset="", answer=AsyncMock())
    await inline.search_inline(query)
    assert len(query.answer.await_args.kwargs["results"]) == 50
    assert query.answer.await_args.kwargs["next_offset"] == "50"
    query.offset = "50"
    await inline.search_inline(query)
    assert len(query.answer.await_args.kwargs["results"]) == 5
    assert query.answer.await_args.kwargs["next_offset"] == ""
    query.query, query.offset = "Alias 3", ""
    await inline.search_inline(query)
    assert [item.title for item in query.answer.await_args.kwargs["results"]] == ["Series 3"]
    query.offset = "bad"
    await inline.search_inline(query)
    assert query.answer.await_args.kwargs["results"] == []
