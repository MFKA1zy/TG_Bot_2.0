from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMessage
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from services.broadcast_service import BroadcastService


@pytest.mark.asyncio
async def test_broadcast_retries_after_telegram_rate_limit():
    service = BroadcastService()
    bot = AsyncMock()
    bot.send_message.side_effect = [
        TelegramRetryAfter(
            method=SendMessage(chat_id=1, text="hello"),
            message="rate limited",
            retry_after=0,
        ),
        object(),
    ]

    result = await service.send_broadcast(bot, [1], "hello")

    assert result["success"] == 1
    assert result["failed"] == 0
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_broadcast_continues_after_blocked_user_and_marks_them():
    service = BroadcastService()
    bot = AsyncMock()
    bot.send_message.side_effect = [
        TelegramForbiddenError(
            method=SendMessage(chat_id=1, text="hello"),
            message="bot was blocked",
        ),
        object(),
    ]
    marked_blocked = AsyncMock()

    result = await service.send_broadcast(
        bot,
        [1, 2],
        "hello",
        on_blocked=marked_blocked,
    )

    assert result["success"] == 1
    assert result["failed"] == 1
    marked_blocked.assert_awaited_once_with(1)
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_broadcast_can_be_cancelled_between_recipients():
    service = BroadcastService()
    bot = AsyncMock()

    async def send_and_cancel(**kwargs):
        service.cancel()
        return object()

    bot.send_message.side_effect = send_and_cancel

    result = await service.send_broadcast(bot, [1, 2], "hello")

    assert result["cancelled"] is True
    assert result["processed"] == 1
    assert bot.send_message.await_count == 1
    assert service.is_running is False
