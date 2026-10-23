from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers.captcha as captcha_handlers
import handlers.start as start_handlers


def make_message(language_code="en"):
    chat = SimpleNamespace(id=101)
    response = SimpleNamespace(chat=chat, message_id=201)
    return SimpleNamespace(
        chat=chat,
        message_id=100,
        from_user=SimpleNamespace(
            id=101,
            username="user",
            first_name="User",
            last_name=None,
            language_code=language_code,
        ),
        bot=object(),
        answer=AsyncMock(return_value=response),
    )


@pytest.mark.asyncio
async def test_first_start_asks_for_language_before_starting_captcha(monkeypatch):
    message = make_message("uk")
    state = AsyncMock()
    user = SimpleNamespace(language=None, is_admin=False, captcha_passed=False)
    monkeypatch.setattr(
        start_handlers.repository,
        "ensure_user",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "track_user_message",
        lambda *args: None,
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "clear_chat",
        AsyncMock(),
    )
    start_captcha = AsyncMock()
    monkeypatch.setattr(captcha_handlers, "start_captcha", start_captcha)

    await start_handlers.command_start(message, state)

    message.answer.assert_awaited_once()
    assert "Оберіть мову" in message.answer.await_args.args[0]
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "set_language:ru"
    start_captcha.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_language_start_runs_captcha_before_welcome(monkeypatch):
    message = make_message("en")
    state = AsyncMock()
    user = SimpleNamespace(language="en", is_admin=False, captcha_passed=False)
    monkeypatch.setattr(
        start_handlers.repository,
        "ensure_user",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "track_user_message",
        lambda *args: None,
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "clear_chat",
        AsyncMock(),
    )
    start_captcha = AsyncMock()
    monkeypatch.setattr(captcha_handlers, "start_captcha", start_captcha)

    await start_handlers.command_start(message, state)

    start_captcha.assert_awaited_once_with(message, state)
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_user_receives_localized_welcome_and_inline_only_search(monkeypatch):
    message = make_message("en")
    user = SimpleNamespace(language="en", is_admin=False, captcha_passed=True)
    monkeypatch.setattr(
        start_handlers.repository,
        "ensure_user",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        start_handlers.repository,
        "get_user_by_telegram_id",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "track_user_message",
        lambda *args: None,
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "track_message",
        lambda *args: None,
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "clear_chat",
        AsyncMock(),
    )
    monkeypatch.setattr(
        start_handlers,
        "get_welcome_text",
        lambda language: "Welcome to Series Search TV!",
    )

    await start_handlers.command_start(message, AsyncMock())

    text, = message.answer.await_args.args
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert "Welcome" in text
    assert len(keyboard.inline_keyboard) == 3
    assert keyboard.inline_keyboard[2][0].callback_data == "cat:home"
    assert keyboard.inline_keyboard[0][0].switch_inline_query_current_chat == ""
    assert keyboard.inline_keyboard[1][0].callback_data == "user_settings"


@pytest.mark.asyncio
async def test_settings_button_renders_current_language_without_error(monkeypatch):
    callback_message = SimpleNamespace(
        chat=SimpleNamespace(id=101),
        message_id=201,
        bot=object(),
    )
    callback = SimpleNamespace(
        message=callback_message,
        from_user=SimpleNamespace(id=101),
        answer=AsyncMock(),
    )
    user = SimpleNamespace(language="en")
    replacement = SimpleNamespace(chat=callback_message.chat, message_id=201)
    monkeypatch.setattr(
        start_handlers.repository,
        "get_user_by_telegram_id",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        start_handlers,
        "replace_with_text",
        AsyncMock(return_value=replacement),
    )
    monkeypatch.setattr(
        start_handlers.message_service,
        "track_message",
        lambda *args: None,
    )

    await start_handlers.user_settings(callback)

    text = start_handlers.replace_with_text.await_args.args[1]
    assert "Current language: 🇬🇧 English" in text
    assert callback.answer.await_count == 1
