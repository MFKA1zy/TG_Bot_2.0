from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember

import services.access_service as access
import handlers.inline_search as inline


@pytest.mark.asyncio
@pytest.mark.parametrize("status,is_member,expected", [
    ("creator", None, True), ("administrator", None, True),
    ("member", None, True), ("restricted", True, True),
    ("restricted", False, False), ("restricted", None, False),
    ("left", None, False), ("kicked", None, False),
])
async def test_membership_states(monkeypatch, status, is_member, expected):
    monkeypatch.setattr(access, "settings", SimpleNamespace(SUBSCRIPTION_REQUIRED=True, SUBSCRIPTION_CHANNEL_ID=-100123))
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status, is_member=is_member)))
    assert await access.has_active_subscription(bot, 51) is expected
    bot.get_chat_member.assert_awaited_once_with(-100123, 51)


@pytest.mark.asyncio
async def test_enabled_subscription_without_channel_denies_access(monkeypatch):
    monkeypatch.setattr(access, "settings", SimpleNamespace(SUBSCRIPTION_REQUIRED=True, SUBSCRIPTION_CHANNEL_ID=None))
    assert await access.has_active_subscription(None, 51) is False


@pytest.mark.asyncio
async def test_telegram_failure_denies_access(monkeypatch):
    monkeypatch.setattr(access, "settings", SimpleNamespace(SUBSCRIPTION_REQUIRED=True, SUBSCRIPTION_CHANNEL_ID="@channel"))
    error = TelegramBadRequest(method=GetChatMember(chat_id="@channel", user_id=51), message="chat not found")
    bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=error))
    assert await access.has_active_subscription(bot, 51) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked,captcha,subscribed", [(True, True, True), (False, False, True), (False, True, False)])
async def test_inline_denied_users_never_read_catalog(monkeypatch, blocked, captcha, subscribed):
    monkeypatch.setattr(inline.repository, "get_user_by_telegram_id", AsyncMock(return_value=SimpleNamespace(language="en", is_blocked=blocked, captcha_passed=captcha)))
    monkeypatch.setattr(inline, "settings", SimpleNamespace(SUBSCRIPTION_REQUIRED=True))
    monkeypatch.setattr(inline, "has_active_subscription", AsyncMock(return_value=subscribed))
    catalog = AsyncMock(side_effect=AssertionError("Unauthorized catalog access"))
    monkeypatch.setattr(inline.series_service, "list_series", catalog)
    query = SimpleNamespace(from_user=SimpleNamespace(id=51, language_code="en"), bot=object(), query="", answer=AsyncMock())
    await inline.search_inline(query)
    result = query.answer.await_args.kwargs
    assert result["results"] == []
    assert result["cache_time"] == 0
    assert result["is_personal"] is True
    if not blocked:
        assert result["button"].start_parameter in {"register", "subscription"}
    catalog.assert_not_called()
