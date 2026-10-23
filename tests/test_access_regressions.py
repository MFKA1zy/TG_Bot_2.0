from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, Update, User

import handlers.captcha as captcha
import handlers.start as start
import services.i18n as i18n
from services.access_middleware import BlockedUserMiddleware
from database.repository import UserRepository


def bot_message():
    return Message(message_id=1, date=datetime.now(timezone.utc),
                   chat=Chat(id=51, type="private"),
                   from_user=User(id=999, is_bot=True, first_name="Bot"))


@pytest.mark.asyncio
async def test_captcha_from_bot_message_uses_private_chat_owner(monkeypatch):
    lookup = AsyncMock(return_value=SimpleNamespace(language="en", captcha_passed=True))
    monkeypatch.setattr(captcha.repository, "get_user_by_telegram_id", lookup)
    monkeypatch.setattr(captcha.message_service, "clear_chat", AsyncMock())
    welcome = AsyncMock()
    monkeypatch.setattr(captcha, "show_welcome", welcome)
    message = bot_message()
    await captcha.start_captcha(message, AsyncMock())
    lookup.assert_awaited_once_with(51)
    welcome.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_stale_subscription_callback_cannot_skip_captcha(monkeypatch):
    monkeypatch.setattr(start.repository, "get_user_by_telegram_id", AsyncMock(return_value=SimpleNamespace(language="en", captcha_passed=False, is_blocked=False)))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    await start.show_welcome(bot_message())
    assert answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "set_language:ru"


@pytest.mark.asyncio
async def test_update_locale_preserves_dispatcher_event_and_restores_context(monkeypatch):
    monkeypatch.setattr(UserRepository, "get_user_by_telegram_id", AsyncMock(return_value=SimpleNamespace(language="uk")))
    update = Update(update_id=1, message=bot_message())
    before = i18n.normalize_language(None)
    async def handler(event, data):
        assert event is update
        assert i18n.normalize_language(None) == "uk"
        return 42
    assert await i18n.LocaleMiddleware()(handler, update, {}) == 42
    assert i18n.normalize_language(None) == before


@pytest.mark.asyncio
async def test_blocked_user_middleware_stops_dispatch(monkeypatch):
    monkeypatch.setattr(UserRepository, "get_user_by_telegram_id", AsyncMock(return_value=SimpleNamespace(language="en", is_blocked=True)))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    handler = AsyncMock()
    await BlockedUserMiddleware()(handler, Update(update_id=1, message=bot_message()), {})
    handler.assert_not_awaited()
    answer.assert_awaited_once_with(i18n.tr("en", "access_blocked"))
