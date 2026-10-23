from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import settings
from database.repository import UserRepository
from handlers.start import show_welcome
from keyboards.inline.captcha import build_retry_captcha_button
from services.captcha_service import CaptchaService
from services.message_service import message_service
from services.i18n import normalize_language, tr
from states.captcha import CaptchaStates

logger = logging.getLogger(__name__)
router = Router()
repository = UserRepository()
captcha_service = CaptchaService(
    settings.CAPTCHA_ASSET_DIR,
    settings.CAPTCHA_ANSWERS_PATH,
)


def _is_blocked(blocked_until: str | None) -> bool:
    if not blocked_until:
        return False
    try:
        expiry = datetime.fromisoformat(blocked_until)
    except ValueError:
        logger.warning("Invalid CAPTCHA lock timestamp in the database.")
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expiry


async def _send_and_track(message: Message, text: str, **kwargs) -> Message:
    response = await message.answer(text, **kwargs)
    message_service.track_message(response.chat.id, response.message_id)
    return response


async def start_captcha(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return

    await message_service.clear_chat(message.bot, message.chat.id)
    user = await repository.get_user_by_telegram_id(message.from_user.id)
    language = normalize_language(user.language if user else None)
    if user and user.captcha_passed:
        await state.clear()
        await show_welcome(message)
        return

    captcha_state = await repository.get_captcha_state(message.from_user.id)
    if _is_blocked(captcha_state.get("blocked_until")):
        await state.clear()
        await _send_and_track(
            message,
            tr(language, "captcha_locked"),
            reply_markup=build_retry_captcha_button(language),
        )
        return
    if captcha_state.get("attempts", 0) >= 5:
        await repository.reset_captcha_state(message.from_user.id)

    challenge = captcha_service.get_challenge()
    if challenge is None:
        logger.error("CAPTCHA challenge unavailable; check image and answer files.")
        await state.clear()
        await _send_and_track(
            message,
            tr(language, "captcha_unavailable"),
            reply_markup=build_retry_captcha_button(language),
        )
        return

    photo_path = settings.CAPTCHA_ASSET_DIR / challenge["image_file"]
    try:
        response = await message.answer_photo(
            photo=FSInputFile(str(photo_path)),
            caption=tr(language, "captcha_prompt"),
        )
    except (OSError, ValueError, TelegramAPIError):
        logger.exception("Unable to send CAPTCHA image.")
        await state.clear()
        await _send_and_track(
            message,
            tr(language, "captcha_unavailable"),
            reply_markup=build_retry_captcha_button(language),
        )
        return

    message_service.track_message(message.chat.id, response.message_id)
    await state.set_state(CaptchaStates.waiting_for_answer)
    await state.update_data(
        image_file=challenge["image_file"],
    )


@router.message(F.chat.type == "private", CaptchaStates.waiting_for_answer)
async def process_captcha(message: Message, state: FSMContext) -> None:
    message_service.track_user_message(message.chat.id, message.message_id)
    previous_feedback_id = (await state.get_data()).get("feedback_message_id")
    if isinstance(previous_feedback_id, int):
        await message_service.safe_delete(
            message.bot,
            message.chat.id,
            previous_feedback_id,
        )
    user = await repository.ensure_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    language = normalize_language(user.language)
    if user.captcha_passed:
        await state.clear()
        await message_service.clear_chat(message.bot, message.chat.id)
        await show_welcome(message)
        return

    captcha_state = await repository.get_captcha_state(message.from_user.id)
    if _is_blocked(captcha_state.get("blocked_until")):
        await state.clear()
        await _send_and_track(
            message,
            tr(language, "captcha_locked"),
            reply_markup=build_retry_captcha_button(language),
        )
        return

    data = await state.get_data()
    image_file = data.get("image_file")
    if not image_file:
        await state.clear()
        await _send_and_track(
            message,
            tr(language, "captcha_stale"),
            reply_markup=build_retry_captcha_button(language),
        )
        return

    answer = message.text or ""
    if captcha_service.validate(image_file, answer):
        await repository.set_captcha_passed(message.from_user.id, True)
        await repository.reset_captcha_state(message.from_user.id)
        await state.clear()
        await message_service.clear_chat(message.bot, message.chat.id)
        await show_welcome(message)
        return

    attempts = await repository.increment_captcha_attempts(message.from_user.id)
    if attempts >= 5:
        blocked_until = (
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ).isoformat(timespec="seconds")
        await repository.update_captcha_state(
            message.from_user.id, attempts, blocked_until
        )
        await state.clear()
        await _send_and_track(
            message,
            tr(language, "captcha_too_many"),
            reply_markup=build_retry_captcha_button(language),
        )
        return

    response = await _send_and_track(
        message,
        tr(language, "captcha_wrong", attempts=5 - attempts),
    )
    await state.update_data(feedback_message_id=response.message_id)


@router.callback_query(F.data == "restart_captcha")
async def restart_captcha(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await message_service.clear_chat(
        callback.bot,
        callback.message.chat.id,
    )
    await start_captcha(callback.message, state)


@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext) -> None:
    user = await repository.get_user_by_telegram_id(callback.from_user.id)
    await callback.answer(tr(user.language if user else None, "admin_cancel"))
    await state.clear()
    await message_service.clear_chat(
        callback.bot,
        callback.message.chat.id,
    )
