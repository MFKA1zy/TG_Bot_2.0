from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import settings
from database.repository import UserRepository
from keyboards.inline.admin_menu import build_admin_menu
from services.message_service import message_service
from services.i18n import normalize_language, tr
from services.series_service import CatalogError, SeriesService
from states.admin import AdminStates
from utils.telegram import replace_with_text

logger = logging.getLogger(__name__)
router = Router()
repository = UserRepository()
series_service = SeriesService()


def _sender_id(event: Message | CallbackQuery) -> int:
    return event.from_user.id


async def get_admin_language(event: Message | CallbackQuery) -> str:
    user = await repository.get_user_by_telegram_id(_sender_id(event))
    return normalize_language(user.language if user else None)


async def ensure_admin(event: Message | CallbackQuery) -> bool:
    chat_type = (
        event.chat.type
        if isinstance(event, Message)
        else event.message.chat.type
    )
    if chat_type != "private":
        if isinstance(event, Message):
            await event.answer(tr(await get_admin_language(event), "admin_private_only"))
        else:
            await event.answer(
                tr(await get_admin_language(event), "admin_private_only"),
                show_alert=True,
            )
        return False
    user = await repository.get_user_by_telegram_id(_sender_id(event))
    if user and user.is_admin:
        return True
    if isinstance(event, Message):
        await event.answer(tr(await get_admin_language(event), "admin_access_denied"))
    else:
        await event.answer(
            tr(await get_admin_language(event), "admin_access_denied"),
            show_alert=True,
        )
    return False


@router.message(F.chat.type == "private", Command("activate_admin"))
async def begin_admin_activation(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await repository.ensure_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    if user.is_admin:
        await message.answer(tr(await get_admin_language(message), "admin_already"))
        return
    if (
        not settings.ADMIN_CODE_ACTIVATION_ENABLED
        or not settings.ADMIN_ACTIVATION_CODE
        or message.from_user.id not in settings.ADMIN_IDS
    ):
        await message.answer(
            tr(await get_admin_language(message), "admin_activation_unavailable")
        )
        return
    activation = await repository.get_admin_activation_state(message.from_user.id)
    blocked_until = activation.get("blocked_until")
    if blocked_until:
        try:
            expiry = datetime.fromisoformat(blocked_until)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < expiry:
                await message.answer(
                    tr(await get_admin_language(message), "admin_retry_after")
                )
                return
        except ValueError:
            logger.warning("Invalid administrator activation lock timestamp.")
        else:
            await repository.update_admin_activation_state(message.from_user.id, 0)
    await message.answer(
        tr(await get_admin_language(message), "admin_activation_prompt")
    )
    await state.set_state(AdminStates.waiting_activation_code)


@router.message(F.chat.type == "private", AdminStates.waiting_activation_code)
async def process_admin_activation(message: Message, state: FSMContext) -> None:
    await repository.ensure_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    await message_service.safe_delete_user_message(
        message.bot,
        message.chat.id,
        message.message_id,
    )
    if (
        not settings.ADMIN_CODE_ACTIVATION_ENABLED
        or not settings.ADMIN_ACTIVATION_CODE
        or message.from_user.id not in settings.ADMIN_IDS
    ):
        await state.clear()
        return
    activation = await repository.get_admin_activation_state(message.from_user.id)
    blocked_until = activation.get("blocked_until")
    if blocked_until:
        try:
            expiry = datetime.fromisoformat(blocked_until)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < expiry:
                await state.clear()
                await message.answer(
                    tr(await get_admin_language(message), "admin_retry_after")
                )
                return
        except ValueError:
            logger.warning("Invalid administrator activation lock timestamp.")
        else:
            await repository.update_admin_activation_state(message.from_user.id, 0)

    candidate = message.text or ""
    if hmac.compare_digest(candidate, settings.ADMIN_ACTIVATION_CODE):
        await repository.set_admin(message.from_user.id, True)
        await repository.update_admin_activation_state(message.from_user.id, 0)
        await state.clear()
        await message.answer(
            tr(await get_admin_language(message), "admin_activated"),
            reply_markup=build_admin_menu(await get_admin_language(message)),
        )
        return

    attempts = await repository.increment_admin_activation_attempts(
        message.from_user.id
    )
    if attempts >= 5:
        blocked = (
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ).isoformat(timespec="seconds")
        await repository.update_admin_activation_state(
            message.from_user.id,
            attempts,
            blocked,
        )
        await state.clear()
        await message.answer(
            tr(await get_admin_language(message), "admin_retry_after")
        )
        return
    await message.answer(
        tr(
            await get_admin_language(message),
            "admin_invalid_code",
            attempts=5 - attempts,
        )
    )


@router.message(F.chat.type == "private", Command("admin"))
async def admin_panel(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        return
    await state.clear()
    message_service.track_user_message(message.chat.id, message.message_id)
    await message_service.safe_delete_user_message(
        message.bot,
        message.chat.id,
        message.message_id,
    )
    response = await message.answer(
        tr(await get_admin_language(message), "admin_title"),
        reply_markup=build_admin_menu(await get_admin_language(message)),
    )
    message_service.track_message(response.chat.id, response.message_id)


@router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    await state.clear()
    language = await get_admin_language(callback)
    response = await replace_with_text(
        callback.message,
        tr(language, "admin_title"),
        reply_markup=build_admin_menu(language),
    )
    message_service.track_message(response.chat.id, response.message_id)


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    language = await get_admin_language(callback)
    stats = await repository.get_stats()
    try:
        series_count = series_service.count_series()
    except CatalogError:
        logger.exception("Catalog unavailable while generating admin statistics.")
        await replace_with_text(
            callback.message,
            tr(language, "catalog_stats_unavailable"),
            reply_markup=build_admin_menu(language),
        )
        return
    text = (
        tr(
            language,
            "admin_statistics",
            total=stats["total_users"],
            captcha=stats["captcha_passed"],
            admins=stats["admins"],
            series=series_count,
            today=stats["new_today"],
            week=stats["new_last_7_days"],
            blocked=stats["blocked_users"],
            broadcasts=stats["broadcasts"],
        )
    )
    await replace_with_text(
        callback.message, text, reply_markup=build_admin_menu(language)
    )


@router.callback_query(F.data == "admin_cancel")
async def cancel_admin_operation(
    callback: CallbackQuery, state: FSMContext
) -> None:
    language = await get_admin_language(callback)
    await callback.answer(tr(language, "admin_cancelled"))
    await state.clear()
    await message_service.clear_chat(
        callback.bot,
        callback.message.chat.id,
        preserve_message_ids={callback.message.message_id},
    )
    response_message = await replace_with_text(
        callback.message,
        tr(language, "admin_cancelled"),
    )
    user = await repository.get_user_by_telegram_id(callback.from_user.id)
    if user and user.is_admin:
        response_message = await replace_with_text(
            response_message,
            tr(language, "admin_title"),
            reply_markup=build_admin_menu(language),
        )
    message_service.track_message(
        response_message.chat.id,
        response_message.message_id,
    )


@router.message(Command("cancel"))
async def cancel_operation(message: Message, state: FSMContext) -> None:
    await state.clear()
    message_service.track_user_message(message.chat.id, message.message_id)
    await message_service.clear_chat(message.bot, message.chat.id)
    user = await repository.get_user_by_telegram_id(message.from_user.id)
    language = normalize_language(user.language if user else None)
    if user and user.is_admin:
        response = await message.answer(
            tr(language, "admin_cancelled"),
            reply_markup=build_admin_menu(language),
        )
    else:
        response = await message.answer(tr(language, "admin_cancelled"))
    message_service.track_message(response.chat.id, response.message_id)
