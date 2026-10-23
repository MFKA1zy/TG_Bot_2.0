from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database.repository import UserRepository
from handlers.admin.panel import ensure_admin
from keyboards.inline.admin_menu import (
    build_admin_confirmation,
    build_admin_menu,
    build_welcome_language_menu,
)
from services.settings_service import (
    SettingsFileError,
    get_welcome_text,
    save_welcome_text,
)
from services.message_service import message_service
from services.i18n import normalize_language, tr
from states.admin import AdminStates
from utils.telegram import edit_tracked_text, replace_with_text

logger = logging.getLogger(__name__)
router = Router()
repository = UserRepository()


async def _admin_language(event: Message | CallbackQuery) -> str:
    user = await repository.get_user_by_telegram_id(event.from_user.id)
    return normalize_language(user.language if user else None)


async def _update_welcome_prompt(
    message: Message,
    state: FSMContext,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    prompt_id = (await state.get_data()).get("active_prompt_message_id")
    if isinstance(prompt_id, int):
        response = await edit_tracked_text(
            message.bot,
            message.chat.id,
            prompt_id,
            text,
            reply_markup=reply_markup,
        )
    else:
        response = await message.answer(text, reply_markup=reply_markup)
        message_service.track_message(response.chat.id, response.message_id)
    await state.update_data(active_prompt_message_id=response.message_id)
    return response


@router.callback_query(F.data == "admin_edit_welcome")
async def edit_welcome(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    language = await _admin_language(callback)
    await state.clear()
    response = await replace_with_text(
        callback.message,
        tr(language, "admin_select_language_welcome"),
        reply_markup=build_welcome_language_menu(language),
    )
    message_service.track_message(response.chat.id, response.message_id)


@router.callback_query(F.data.startswith("admin_welcome_lang:"))
async def select_welcome_language(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    selected_language = callback.data.partition(":")[2]
    if selected_language not in ("ru", "uk", "en"):
        await callback.answer()
        return
    await callback.answer()
    language = await _admin_language(callback)
    try:
        text = get_welcome_text(selected_language)
    except SettingsFileError:
        logger.exception("Unable to read configured welcome text.")
        await replace_with_text(
            callback.message,
            tr(language, "admin_welcome_error"),
            reply_markup=build_admin_menu(language),
        )
        return
    response = await replace_with_text(
        callback.message,
        tr(
            language,
            "admin_current_welcome",
            language_name=tr(language, f"language_{selected_language}"),
            text=html.escape(text),
        ),
    )
    await state.clear()
    await state.update_data(active_prompt_message_id=response.message_id)
    await state.update_data(welcome_language=selected_language)
    await state.set_state(AdminStates.waiting_welcome_text)


@router.message(AdminStates.waiting_welcome_text)
async def preview_welcome(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    language = await _admin_language(message)
    new_text = (message.text or "").strip()
    message_service.track_user_message(message.chat.id, message.message_id)
    await message_service.safe_delete_user_message(
        message.bot,
        message.chat.id,
        message.message_id,
    )
    if not new_text:
        await _update_welcome_prompt(
            message,
            state,
            tr(language, "admin_empty_welcome"),
        )
        return
    if len(new_text) > 3500:
        await _update_welcome_prompt(
            message,
            state,
            tr(language, "admin_long_welcome"),
        )
        return
    await state.update_data(welcome_text=new_text)
    await state.set_state(AdminStates.waiting_welcome_confirmation)
    await _update_welcome_prompt(
        message,
        state,
        tr(language, "admin_preview_welcome", text=html.escape(new_text)),
        reply_markup=build_admin_confirmation("admin_confirm_welcome", language),
    )


@router.callback_query(F.data == "admin_confirm_welcome")
async def confirm_welcome(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    language = await _admin_language(callback)
    if await state.get_state() != AdminStates.waiting_welcome_confirmation.state:
        await replace_with_text(
            callback.message,
            tr(language, "admin_welcome_stale"),
            reply_markup=build_admin_menu(language),
        )
        return
    text = (await state.get_data()).get("welcome_text")
    if not isinstance(text, str) or not text.strip():
        await state.clear()
        await replace_with_text(
            callback.message,
            tr(language, "admin_welcome_stale"),
            reply_markup=build_admin_menu(language),
        )
        return
    try:
        welcome_language = (await state.get_data()).get("welcome_language")
        save_welcome_text(text, welcome_language)
    except (SettingsFileError, ValueError):
        logger.exception("Unable to save configured welcome text.")
        await state.clear()
        await replace_with_text(
            callback.message,
            tr(language, "admin_welcome_error"),
            reply_markup=build_admin_menu(language),
        )
        return
    await state.clear()
    await replace_with_text(
        callback.message,
        tr(language, "admin_welcome_saved"),
        reply_markup=build_admin_menu(language),
    )
