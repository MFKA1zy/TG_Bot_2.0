from unittest.mock import AsyncMock

import pytest

from services.message_service import MessageService


@pytest.mark.asyncio
async def test_clear_chat_deletes_only_explicitly_tracked_messages(tmp_path):
    service = MessageService(tmp_path / "tracked.json")
    bot = AsyncMock()
    service.track_message(10, 100)
    service.track_user_message(10, 200)

    await service.clear_chat(bot, 10)

    assert bot.delete_message.await_count == 2
    bot.delete_message.assert_any_await(10, 100)
    bot.delete_message.assert_any_await(10, 200)
    assert not service._tracked_messages
    assert not service._tracked_user_messages


@pytest.mark.asyncio
async def test_clear_chat_continues_after_telegram_delete_failure(tmp_path):
    from aiogram.methods import DeleteMessage
    from aiogram.exceptions import TelegramAPIError

    service = MessageService(tmp_path / "tracked.json")
    bot = AsyncMock()
    bot.delete_message.side_effect = [
        TelegramAPIError(
            method=DeleteMessage(chat_id=10, message_id=100),
            message="cannot delete",
        ),
        True,
    ]
    service.track_message(10, 100)
    service.track_message(10, 101)

    await service.clear_chat(bot, 10)

    assert bot.delete_message.await_count == 2
    assert service._tracked_messages[10] == {100}


@pytest.mark.asyncio
async def test_clear_chat_keeps_failed_deletions_for_the_next_cleanup(tmp_path):
    from aiogram.methods import DeleteMessage
    from aiogram.exceptions import TelegramAPIError

    service = MessageService(tmp_path / "tracked.json")
    bot = AsyncMock()
    bot.delete_message.side_effect = [
        TelegramAPIError(
            method=DeleteMessage(chat_id=10, message_id=100),
            message="temporary network failure",
        ),
        True,
    ]
    service.track_message(10, 100)

    await service.clear_chat(bot, 10)
    assert service._tracked_messages[10] == {100}

    await service.clear_chat(bot, 10)
    assert 10 not in service._tracked_messages
    assert bot.delete_message.await_count == 2


@pytest.mark.asyncio
async def test_clear_chat_drops_message_already_removed_in_telegram(tmp_path):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import DeleteMessage

    service = MessageService(tmp_path / "tracked.json")
    bot = AsyncMock()
    bot.delete_message.side_effect = TelegramBadRequest(
        method=DeleteMessage(chat_id=10, message_id=100),
        message="Message to delete not found",
    )
    service.track_message(10, 100)

    await service.clear_chat(bot, 10)

    assert not service._tracked_messages


@pytest.mark.asyncio
async def test_clear_chat_forgets_messages_telegram_says_are_too_old(tmp_path):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import DeleteMessage

    service = MessageService(tmp_path / "tracked.json")
    bot = AsyncMock()
    bot.delete_message.side_effect = TelegramBadRequest(
        method=DeleteMessage(chat_id=10, message_id=100),
        message="Message can't be deleted",
    )
    service.track_message(10, 100)

    await service.clear_chat(bot, 10)

    assert 10 not in service._tracked_messages


@pytest.mark.asyncio
async def test_clear_chat_can_preserve_the_message_being_edited(tmp_path):
    service = MessageService(tmp_path / "tracked.json")
    bot = AsyncMock()
    service.track_message(10, 100)
    service.track_message(10, 101)

    await service.clear_chat(bot, 10, preserve_message_ids={101})

    bot.delete_message.assert_awaited_once_with(10, 100)
    assert service._tracked_messages[10] == {101}


def test_tracked_messages_survive_service_restart(tmp_path):
    tracking_path = tmp_path / "tracked.json"
    service = MessageService(tracking_path)
    service.track_message(10, 100)
    service.track_user_message(10, 200)

    restarted_service = MessageService(tracking_path)

    assert restarted_service._tracked_messages == {10: {100}}
    assert restarted_service._tracked_user_messages == {10: {200}}
