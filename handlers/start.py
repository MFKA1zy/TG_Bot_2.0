from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.repository import UserRepository
from keyboards.inline.user_menu import (
    build_language_menu,
    build_main_menu,
    build_settings_menu,
)
from services.i18n import detect_language, normalize_language, tr
from services.message_service import message_service
from services.settings_service import SettingsFileError, get_welcome_text
from utils.telegram import replace_with_text

logger = logging.getLogger(__name__)
router = Router()
repository = UserRepository()


async def show_welcome(message: Message | CallbackQuery) -> None:
    user = await repository.get_user_by_telegram_id(message.from_user.id)
    language = normalize_language(user.language if user else None)
    try:
        text = html.escape(get_welcome_text(language)).replace(
            "Series Search TV",
            "<b>Series Search TV</b>",
        )
    except SettingsFileError:
        logger.exception("Unable to show the configured welcome message.")
        text = tr(language, "welcome_unavailable")

    if isinstance(message, CallbackQuery):
        await message_service.clear_chat(
            message.bot,
            message.message.chat.id,
            preserve_message_ids={message.message.message_id},
        )
        response = await replace_with_text(
            message.message,
            text,
            reply_markup=build_main_menu(language),
        )
        message_service.track_message(response.chat.id, response.message_id)
        return
    response = await message.answer(text, reply_markup=build_main_menu(language))
    message_service.track_message(response.chat.id, response.message_id)


@router.message(F.chat.type == "private", CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    message_service.track_user_message(message.chat.id, message.message_id)
    await message_service.clear_chat(message.bot, message.chat.id)
    user = await repository.ensure_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    if not user.language:
        await message.answer(
            tr(detect_language(message.from_user.language_code), "language_prompt"),
            reply_markup=build_language_menu(),
        )
        return
    if not user.captcha_passed:
        from handlers.captcha import start_captcha

        await start_captcha(message, state)
        return
    await show_welcome(message)


@router.callback_query(F.data.startswith("set_language:"))
async def set_language(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    language = callback.data.partition(":")[2]
    if language not in ("ru", "uk", "en"):
        await callback.answer()
        return
    await callback.answer()
    await repository.set_language(callback.from_user.id, language)
    await state.clear()
    user = await repository.get_user_by_telegram_id(callback.from_user.id)
    if user and user.captcha_passed:
        await show_welcome(callback)
        return
    await message_service.clear_chat(
        callback.bot,
        callback.message.chat.id,
        preserve_message_ids={callback.message.message_id},
    )
    from handlers.captcha import start_captcha

    await start_captcha(callback.message, state)


@router.callback_query(F.data == "user_settings")
async def user_settings(callback: CallbackQuery) -> None:
    await callback.answer()
    user = await repository.get_user_by_telegram_id(callback.from_user.id)
    language = normalize_language(user.language if user else None)
    response = await replace_with_text(
        callback.message,
        tr(
            language,
            "settings_title",
            language_name=tr(language, f"language_{language}"),
        ),
        reply_markup=build_settings_menu(language),
    )
    message_service.track_message(response.chat.id, response.message_id)


@router.callback_query(F.data == "change_language")
async def change_language(callback: CallbackQuery) -> None:
    await callback.answer()
    user = await repository.get_user_by_telegram_id(callback.from_user.id)
    language = normalize_language(user.language if user else None)
    response = await replace_with_text(
        callback.message,
        tr(language, "language_prompt"),
        reply_markup=build_language_menu(language),
    )
    message_service.track_message(response.chat.id, response.message_id)


@router.callback_query(F.data == "main_menu")
async def go_to_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message.chat.type != "private":
        return
    user = await repository.get_user_by_telegram_id(callback.from_user.id)
    if user and not user.language:
        language = detect_language(callback.from_user.language_code)
        await replace_with_text(
            callback.message,
            tr(language, "language_prompt"),
            reply_markup=build_language_menu(language),
        )
        return
    if not user or not user.captcha_passed:
        from handlers.captcha import start_captcha

        await state.clear()
        await start_captcha(callback.message, state)
        return
    await state.clear()
    await show_welcome(callback)
