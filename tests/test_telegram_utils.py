from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageText

from utils.telegram import replace_with_text


@pytest.mark.asyncio
async def test_replace_with_text_deletes_photo_before_sending_text(monkeypatch):
    import utils.telegram as telegram_utils

    result_message = SimpleNamespace(
        chat=SimpleNamespace(id=10),
        message_id=101,
    )
    tracker = Mock()
    monkeypatch.setattr(telegram_utils, "message_service", tracker)
    message = SimpleNamespace(
        photo=[object()],
        delete=AsyncMock(),
        answer=AsyncMock(return_value=result_message),
        edit_text=AsyncMock(),
    )

    result = await replace_with_text(message, "Updated", reply_markup=None)

    assert result is result_message
    tracker.track_message.assert_called_once_with(10, 101)
    message.delete.assert_awaited_once()
    message.answer.assert_awaited_once_with("Updated", reply_markup=None)
    message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_with_text_edits_existing_text_message(monkeypatch):
    import utils.telegram as telegram_utils

    result_message = SimpleNamespace(
        chat=SimpleNamespace(id=10),
        message_id=100,
    )
    tracker = Mock()
    monkeypatch.setattr(telegram_utils, "message_service", tracker)
    message = SimpleNamespace(
        photo=[],
        delete=AsyncMock(),
        answer=AsyncMock(),
        edit_text=AsyncMock(return_value=result_message),
    )

    result = await replace_with_text(message, "Updated")

    assert result is result_message
    tracker.track_message.assert_called_once_with(10, 100)
    message.edit_text.assert_awaited_once_with("Updated", reply_markup=None)
    message.delete.assert_not_awaited()
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_with_text_falls_back_to_caption_when_photo_cannot_be_deleted(
    monkeypatch,
):
    import utils.telegram as telegram_utils

    result_message = SimpleNamespace(
        chat=SimpleNamespace(id=10),
        message_id=100,
    )
    tracker = Mock()
    monkeypatch.setattr(telegram_utils, "message_service", tracker)
    message = SimpleNamespace(
        photo=[object()],
        delete=AsyncMock(
            side_effect=TelegramBadRequest(
                method=DeleteMessage(chat_id=10, message_id=100),
                message="Message can't be deleted",
            )
        ),
        answer=AsyncMock(),
        edit_caption=AsyncMock(return_value=result_message),
    )

    result = await replace_with_text(message, "Updated")

    assert result is result_message
    tracker.track_message.assert_called_once_with(10, 100)
    message.edit_caption.assert_awaited_once_with(
        caption="Updated",
        reply_markup=None,
    )
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_with_text_sends_replacement_if_photo_was_already_deleted(
    monkeypatch,
):
    import utils.telegram as telegram_utils

    result_message = SimpleNamespace(
        chat=SimpleNamespace(id=10),
        message_id=101,
    )
    tracker = Mock()
    monkeypatch.setattr(telegram_utils, "message_service", tracker)
    message = SimpleNamespace(
        photo=[object()],
        delete=AsyncMock(
            side_effect=TelegramBadRequest(
                method=DeleteMessage(chat_id=10, message_id=100),
                message="Message to delete not found",
            )
        ),
        answer=AsyncMock(return_value=result_message),
        edit_caption=AsyncMock(),
    )

    result = await replace_with_text(message, "Updated")

    assert result is result_message
    message.answer.assert_awaited_once_with("Updated", reply_markup=None)
    message.edit_caption.assert_not_awaited()
    tracker.track_message.assert_called_once_with(10, 101)


@pytest.mark.asyncio
async def test_edit_tracked_text_recreates_a_prompt_that_no_longer_exists(
    monkeypatch,
):
    import utils.telegram as telegram_utils

    result_message = SimpleNamespace(
        chat=SimpleNamespace(id=10),
        message_id=102,
    )
    tracker = Mock()
    monkeypatch.setattr(telegram_utils, "message_service", tracker)
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(
            side_effect=TelegramBadRequest(
                method=EditMessageText(
                    chat_id=10,
                    message_id=100,
                    text="Prompt",
                ),
                message="Message to edit not found",
            )
        ),
        send_message=AsyncMock(return_value=result_message),
    )

    result = await telegram_utils.edit_tracked_text(
        bot,
        10,
        100,
        "Fresh prompt",
    )

    assert result is result_message
    bot.send_message.assert_awaited_once_with(
        chat_id=10,
        text="Fresh prompt",
        reply_markup=None,
    )
    tracker.track_message.assert_called_once_with(10, 102)
