from __future__ import annotations

import asyncio
import html
import logging

import aiosqlite
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database.repository import UserRepository
from handlers.admin.panel import ensure_admin
from keyboards.inline.admin_menu import (
    build_admin_menu,
    build_broadcast_confirmation,
    build_broadcast_recovery,
    build_broadcast_running,
)
from services.broadcast_service import BroadcastService
from services.message_service import message_service
from services.i18n import normalize_language, tr
from states.admin import AdminStates
from utils.telegram import edit_tracked_text, replace_with_text

logger = logging.getLogger(__name__)
router = Router()
repository = UserRepository()
broadcast_service = BroadcastService()
_broadcast_task: asyncio.Task | None = None
_broadcast_launch_lock = asyncio.Lock()


async def _update_broadcast_prompt(
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
        await asyncio.to_thread(message_service.track_message, response.chat.id, response.message_id)
    await state.update_data(active_prompt_message_id=response.message_id)
    return response


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    await state.clear()
    response = await replace_with_text(
        callback.message,
        tr(None, "broadcast_prompt")
    )
    await state.update_data(active_prompt_message_id=response.message_id)
    await state.set_state(AdminStates.waiting_broadcast_text)


@router.message(AdminStates.waiting_broadcast_text)
async def receive_broadcast_text(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    text = (message.text or "").strip()
    await asyncio.to_thread(message_service.track_user_message, message.chat.id, message.message_id)
    await message_service.safe_delete_user_message(
        message.bot,
        message.chat.id,
        message.message_id,
    )
    if not text:
        response = await _update_broadcast_prompt(
            message,
            state,
            tr(None, "broadcast_empty"),
        )
        return
    if len(text) > 4000:
        await _update_broadcast_prompt(
            message,
            state,
            tr(None, "broadcast_too_long"),
        )
        return
    user_ids = await repository.get_active_user_ids()
    await state.update_data(broadcast_text=text)
    await _update_broadcast_prompt(
        message,
        state,
        tr(
            None,
            "broadcast_preview",
            text=html.escape(text),
            recipients=len(user_ids),
        ),
        reply_markup=build_broadcast_confirmation(),
    )
    await state.set_state(AdminStates.waiting_broadcast_confirm)


async def _run_broadcast(
    bot,
    chat_id: int,
    status_message_id: int,
    text: str,
    user_ids: list[int],
    run_id: int,
    language: str,
) -> None:
    async def update_progress(progress: dict[str, int]) -> None:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message_id,
            text=tr(
                language,
                "broadcast_progress",
                processed=progress["processed"],
                target=progress["target"],
                success=progress["success"],
                failed=progress["failed"],
            ),
            reply_markup=build_broadcast_running(),
        )

    async def mark_blocked(user_id: int) -> None:
        try:
            await repository.set_delivery_blocked(user_id, True)
        except aiosqlite.Error:
            logger.exception("Could not record blocked-user status for %s.", user_id)

    try:
        result = await broadcast_service.send_broadcast(
            bot,
            user_ids,
            text,
            progress=update_progress,
            on_blocked=mark_blocked,
        )
    except RuntimeError:
        logger.exception("Broadcast could not be started.")
        await bot.send_message(chat_id, tr(language, "broadcast_start_error"))
        return
    try:
        await repository.finish_broadcast_history(
            run_id=run_id,
            success_count=int(result["success"]),
            failed_count=int(result["failed"]),
            processed_count=int(result["processed"]),
            cancelled=bool(result["cancelled"]),
        )
    except aiosqlite.Error:
        logger.exception("Could not persist the broadcast summary.")
        await bot.send_message(
            chat_id,
            tr(language, "broadcast_history_error"),
        )
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message_id,
            text=tr(
                language,
                "broadcast_finished",
                status=tr(
                    language,
                    "broadcast_cancelled" if result["cancelled"] else "broadcast_completed",
                ),
                processed=result["processed"],
                target=result["target"],
                success=result["success"],
                failed=result["failed"],
            ),
            reply_markup=build_admin_menu(language),
        )
    except TelegramAPIError:
        logger.warning("Could not display broadcast summary.", exc_info=True)
        await bot.send_message(
            chat_id,
            (
                tr(
                    language,
                    "broadcast_short_finished",
                    status=tr(
                        language,
                        "broadcast_cancelled"
                        if result["cancelled"]
                        else "broadcast_completed",
                    ),
                    success=result["success"],
                    failed=result["failed"],
                ),
            ),
            reply_markup=build_admin_menu(language),
        )


@router.callback_query(F.data == "admin_confirm_broadcast")
async def confirm_broadcast(
    callback: CallbackQuery, state: FSMContext
) -> None:
    global _broadcast_task
    if not await ensure_admin(callback):
        return
    await callback.answer()
    language = normalize_language(None)
    if await state.get_state() != AdminStates.waiting_broadcast_confirm.state:
        await replace_with_text(
            callback.message,
            tr(language, "broadcast_confirm_stale"),
            reply_markup=build_admin_menu(language),
        )
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not isinstance(text, str) or not text:
        await replace_with_text(
            callback.message,
            tr(language, "broadcast_text_missing"),
            reply_markup=build_admin_menu(language),
        )
        await state.clear()
        return

    async with _broadcast_launch_lock:
        if _broadcast_task and not _broadcast_task.done():
            await replace_with_text(
                callback.message,
                tr(language, "broadcast_already_running"),
            )
            await state.clear()
            return
        if await repository.has_unfinished_broadcast():
            await replace_with_text(
                callback.message,
                tr(language, "broadcast_recovery_needed"),
                reply_markup=build_broadcast_recovery(),
            )
            await state.clear()
            return
        user_ids = await repository.get_active_user_ids()
        run_id = await repository.start_broadcast_history(text, len(user_ids))
        await state.clear()
        response = await replace_with_text(
            callback.message,
            tr(language, "broadcast_starting"),
            reply_markup=build_broadcast_running(),
        )
        _broadcast_task = asyncio.create_task(
            _run_broadcast(
                callback.bot,
                response.chat.id,
                response.message_id,
                text,
                user_ids,
                run_id,
                language,
            )
        )


@router.callback_query(F.data == "admin_recover_broadcast")
async def recover_interrupted_broadcast(callback: CallbackQuery) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    async with _broadcast_launch_lock:
        if broadcast_service.is_running or (
            _broadcast_task is not None and not _broadcast_task.done()
        ):
            await replace_with_text(
                callback.message,
                tr(None, "broadcast_wait_recovery")
            )
            return
        changed = await repository.mark_broadcasts_interrupted()
    await replace_with_text(
        callback.message,
        (
            tr(None, "broadcast_recovered")
            if changed
            else tr(None, "broadcast_no_recovery")
        ),
        reply_markup=build_admin_menu(),
    )


@router.callback_query(F.data == "admin_cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer(
        tr(
            None,
            "broadcast_cancel_requested"
            if broadcast_service.is_running
            else "broadcast_not_running",
        )
    )
    if broadcast_service.is_running:
        broadcast_service.cancel()
