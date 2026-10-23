from __future__ import annotations

import html
import logging
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import settings
from handlers.admin.panel import ensure_admin
from keyboards.inline.admin_menu import (
    build_add_series_photo_prompt,
    build_admin_confirmation,
    build_admin_menu,
    build_admin_series_actions,
    build_admin_series_keyboard,
    build_confirm_remove_series_photo,
    build_delete_confirmation,
    build_edit_series_fields,
    build_edit_series_photo_prompt,
)
from services.image_hosting import ImageHostingError, upload_image_to_imgbb
from services.i18n import tr
from services.message_service import message_service
from services.series_service import CatalogError, SeriesService
from states.admin import AdminStates
from utils.telegram import (
    edit_text_or_caption,
    edit_tracked_text,
    replace_with_text,
)

logger = logging.getLogger(__name__)
router = Router()
series_service = SeriesService()
MAX_TELEGRAM_PHOTO_SIZE = 10 * 1024 * 1024


async def _upload_telegram_photo(bot: Bot, photo_file_id: str) -> str:
    image_buffer = BytesIO()
    try:
        await bot.download(photo_file_id, destination=image_buffer)
    except TelegramAPIError as exc:
        logger.warning(
            "Could not download Telegram series photo (%s).",
            type(exc).__name__,
        )
        raise ImageHostingError(
            "Could not download the Telegram photo."
        ) from exc
    return await upload_image_to_imgbb(image_buffer.getvalue(), settings.IMGBB_API_KEY)


async def _delete_admin_input(message: Message) -> None:
    message_service.track_user_message(message.chat.id, message.message_id)
    await message_service.safe_delete_user_message(
        message.bot,
        message.chat.id,
        message.message_id,
    )


async def _answer_admin(message: Message, text: str, **kwargs) -> Message:
    response = await message.answer(text, **kwargs)
    message_service.track_message(response.chat.id, response.message_id)
    return response


async def _update_admin_prompt(
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
        response = await _answer_admin(message, text, reply_markup=reply_markup)
    await state.update_data(active_prompt_message_id=response.message_id)
    return response


def _add_series_preview(data: dict) -> str:
    photo_status = (
        tr(None, "series_imgbb_photo")
        if data.get("photo_file_id")
        else tr(None, "series_no_photo")
    )
    return tr(
        None,
        "series_add_preview",
        title=html.escape(str(data.get("title", ""))),
        url=html.escape(str(data.get("url", ""))),
        photo_status=photo_status,
    )


async def _show_admin_series(
    callback: CallbackQuery, action: str, page: int = 0
) -> None:
    try:
        items = series_service.list_series()
    except CatalogError:
        logger.exception("Unable to read the catalog for admin actions.")
        await replace_with_text(
            callback.message,
            tr(None, "series_catalog_error"),
            reply_markup=build_admin_menu(),
        )
        return
    if not items:
        await replace_with_text(
            callback.message,
            tr(None, "series_catalog_empty"),
            reply_markup=build_admin_menu(),
        )
        return
    await replace_with_text(
        callback.message,
        tr(None, "series_select"),
        reply_markup=build_admin_series_keyboard(items, action, page),
    )


@router.callback_query(F.data == "admin_add_series")
async def admin_add_series(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    await state.clear()
    response = await replace_with_text(
        callback.message,
        tr(None, "series_enter_title")
    )
    await state.update_data(active_prompt_message_id=response.message_id)
    await state.set_state(AdminStates.waiting_title)


@router.message(AdminStates.waiting_title)
async def admin_add_title(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    title = (message.text or "").strip()
    if not title:
        await _delete_admin_input(message)
        await _update_admin_prompt(
            message,
            state,
            tr(None, "series_empty_title"),
        )
        return
    await state.update_data(title=title)
    await _delete_admin_input(message)
    await _update_admin_prompt(
        message,
        state,
        tr(None, "series_enter_tiktok"),
    )
    await state.set_state(AdminStates.waiting_url)


@router.message(AdminStates.waiting_url)
async def admin_add_url(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    url = (message.text or "").strip()
    try:
        SeriesService.validate_tiktok_url(url)
    except ValueError as exc:
        await _delete_admin_input(message)
        await _update_admin_prompt(
            message,
            state,
            tr(None, "series_invalid_tiktok"),
        )
        return
    data = await state.get_data()
    title = str(data.get("title", ""))
    if not title:
        await _delete_admin_input(message)
        await _update_admin_prompt(
            message,
            state,
            tr(None, "series_add_stale"),
        )
        await state.clear()
        return
    await state.update_data(url=url)
    await _delete_admin_input(message)
    await state.set_state(AdminStates.waiting_series_photo)
    prompt = await _update_admin_prompt(
        message,
        state,
        tr(None, "series_add_photo_prompt"),
        reply_markup=build_add_series_photo_prompt(),
    )
    await state.update_data(photo_prompt_message_id=prompt.message_id)


@router.message(AdminStates.waiting_series_photo, F.photo)
async def receive_add_series_photo(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    photo = message.photo[-1]
    if photo.file_size and photo.file_size > MAX_TELEGRAM_PHOTO_SIZE:
        await _delete_admin_input(message)
        await _update_admin_prompt(
            message,
            state,
            tr(None, "series_photo_too_large"),
            reply_markup=build_add_series_photo_prompt(),
        )
        return
    data = await state.get_data()
    prompt_message_id = data.get("photo_prompt_message_id")
    if isinstance(prompt_message_id, int):
        await message_service.safe_delete(
            message.bot,
            message.chat.id,
            prompt_message_id,
        )
    await state.update_data(photo_file_id=photo.file_id)
    await state.set_state(AdminStates.waiting_confirmation)
    data = await state.get_data()
    response = await message.answer_photo(
        photo=photo.file_id,
        caption=_add_series_preview(data),
        reply_markup=build_admin_confirmation("admin_confirm_save"),
    )
    message_service.track_message(response.chat.id, response.message_id)
    await _delete_admin_input(message)


@router.message(AdminStates.waiting_series_photo)
async def reject_non_photo_series_image(
    message: Message, state: FSMContext
) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    await _delete_admin_input(message)
    await _update_admin_prompt(
        message,
        state,
        tr(None, "series_photo_expected"),
        reply_markup=build_add_series_photo_prompt(),
    )


@router.callback_query(F.data == "admin_skip_series_photo")
async def skip_add_series_photo(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    if await state.get_state() != AdminStates.waiting_series_photo.state:
        await edit_text_or_caption(
            callback.message,
            tr(None, "series_add_stale"),
            reply_markup=build_admin_menu(),
        )
        return
    data = await state.get_data()
    if not data.get("title") or not data.get("url"):
        await state.clear()
        await edit_text_or_caption(
            callback.message,
            tr(None, "series_add_data_missing"),
            reply_markup=build_admin_menu(),
        )
        return
    await state.update_data(photo_file_id=None)
    await state.set_state(AdminStates.waiting_confirmation)
    await edit_text_or_caption(
        callback.message,
        _add_series_preview({**data, "photo_file_id": None}),
        reply_markup=build_admin_confirmation("admin_confirm_save"),
    )


@router.callback_query(F.data == "admin_confirm_save")
async def admin_confirm_save(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    if await state.get_state() != AdminStates.waiting_confirmation.state:
        await replace_with_text(
            callback.message,
            tr(None, "series_preview_stale"),
            reply_markup=build_admin_menu(),
        )
        return
    data = await state.get_data()
    title = data.get("title")
    url = data.get("url")
    photo_url = data.get("photo_url")
    if data.get("photo_file_id") and not photo_url:
        try:
            photo_url = await _upload_telegram_photo(
                callback.bot,
                str(data["photo_file_id"]),
            )
        except ImageHostingError as exc:
            logger.warning("Series poster upload failed: %s", exc)
            await edit_text_or_caption(
                callback.message,
                tr(None, "series_upload_poster_error"),
                reply_markup=build_admin_confirmation("admin_confirm_save"),
            )
            return
        await state.update_data(photo_url=photo_url)
    try:
        if not title or not url:
            raise ValueError("Данные сериала неполные.")
        item = series_service.add_series(
            str(title),
            str(url),
            photo_file_id=data.get("photo_file_id"),
            photo_url=photo_url,
        )
    except (ValueError, CatalogError) as exc:
        logger.info("Series save rejected: %s", exc)
        await replace_with_text(
            callback.message,
            tr(None, "series_save_error"),
            reply_markup=build_admin_menu(),
        )
        await state.clear()
        return
    await state.clear()
    await replace_with_text(
        callback.message,
        tr(None, "series_added", title=html.escape(item["title"])),
        reply_markup=build_admin_menu(),
    )


@router.callback_query(F.data == "admin_list_series")
async def admin_list_series(callback: CallbackQuery) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    await _show_admin_series(callback, "info")


@router.callback_query(F.data == "admin_edit_series")
async def admin_edit_series(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    await state.clear()
    await _show_admin_series(callback, "edit")


@router.callback_query(F.data == "admin_delete_series")
async def admin_delete_series(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    await state.clear()
    await _show_admin_series(callback, "delete")


@router.callback_query(F.data.startswith("admin_series_page:"))
async def admin_series_page(callback: CallbackQuery) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        _, action, page_value = callback.data.split(":")
        page = int(page_value)
    except (AttributeError, ValueError):
        await replace_with_text(
            callback.message,
            tr(None, "series_invalid_page"),
        )
        return
    if action not in {"info", "edit", "delete"} or page < 0:
        await replace_with_text(
            callback.message,
            tr(None, "series_invalid_page"),
        )
        return
    await _show_admin_series(callback, action, page)


@router.callback_query(F.data.startswith("admin_series_info:"))
async def admin_series_info(callback: CallbackQuery) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
        item = series_service.get_series_by_id(series_id)
    except (ValueError, CatalogError):
        logger.exception("Unable to load selected series details.")
        await replace_with_text(
            callback.message,
            tr(None, "series_open_error"),
            reply_markup=build_admin_menu(),
        )
        return
    if not item:
        await replace_with_text(
            callback.message,
            tr(None, "series_missing"),
            reply_markup=build_admin_menu(),
        )
        return
    await replace_with_text(
        callback.message,
        f"<b>{html.escape(str(item['title']))}</b>\n\n"
        + tr(
            None,
            "series_link",
            url=html.escape(str(item["tiktok_url"])),
        ),
        reply_markup=build_admin_series_actions(series_id),
    )


@router.callback_query(F.data.startswith("admin_select_edit:"))
async def admin_select_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
        item = series_service.get_series_by_id(series_id)
    except (ValueError, CatalogError):
        logger.exception("Unable to select series for editing.")
        await replace_with_text(callback.message, tr(None, "series_open_error"))
        return
    if not item:
        await replace_with_text(
            callback.message,
            tr(None, "series_missing"),
            reply_markup=build_admin_menu(),
        )
        return
    await state.clear()
    await state.update_data(series_id=series_id)
    await replace_with_text(
        callback.message,
        tr(
            None,
            "series_edit_question",
            title=html.escape(str(item["title"])),
        ),
        reply_markup=build_edit_series_fields(series_id),
    )


@router.callback_query(F.data.startswith("admin_edit_title:"))
async def begin_edit_title(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
        item = series_service.get_series_by_id(series_id)
    except (ValueError, CatalogError):
        logger.exception("Unable to open series title editor.")
        await replace_with_text(callback.message, tr(None, "series_open_error"))
        return
    if not item:
        await replace_with_text(callback.message, tr(None, "series_missing"))
        return
    await state.clear()
    await state.update_data(series_id=series_id, edit_field="title")
    await state.set_state(AdminStates.waiting_edit_title)
    response = await replace_with_text(
        callback.message,
        tr(None, "series_enter_new_title"),
    )
    await state.update_data(active_prompt_message_id=response.message_id)


@router.callback_query(F.data.startswith("admin_edit_url:"))
async def begin_edit_url(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
        item = series_service.get_series_by_id(series_id)
    except (ValueError, CatalogError):
        logger.exception("Unable to open series URL editor.")
        await replace_with_text(callback.message, tr(None, "series_open_error"))
        return
    if not item:
        await replace_with_text(callback.message, tr(None, "series_missing"))
        return
    await state.clear()
    await state.update_data(series_id=series_id, edit_field="tiktok_url")
    await state.set_state(AdminStates.waiting_edit_url)
    response = await replace_with_text(
        callback.message,
        tr(None, "series_enter_new_url"),
    )
    await state.update_data(active_prompt_message_id=response.message_id)


@router.callback_query(F.data.startswith("admin_edit_photo:"))
async def begin_edit_photo(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
        item = series_service.get_series_by_id(series_id)
    except (ValueError, CatalogError):
        logger.exception("Unable to open series photo editor.")
        await edit_text_or_caption(callback.message, tr(None, "series_open_error"))
        return
    if not item:
        await edit_text_or_caption(
            callback.message,
            tr(None, "series_missing"),
            reply_markup=build_admin_menu(),
        )
        return
    await state.clear()
    await state.update_data(series_id=series_id)
    await state.set_state(AdminStates.waiting_edit_series_photo)
    has_photo = bool(item.get("photo_file_id"))
    await state.update_data(has_photo=has_photo)
    response = await replace_with_text(
        callback.message,
        tr(
            None,
            "series_edit_photo_prompt",
            title=html.escape(str(item["title"])),
        ),
        reply_markup=build_edit_series_photo_prompt(
            series_id,
            has_photo,
        ),
    )
    await state.update_data(
        photo_prompt_message_id=response.message_id,
        active_prompt_message_id=response.message_id,
    )


@router.message(AdminStates.waiting_edit_series_photo, F.photo)
async def receive_edit_series_photo(
    message: Message, state: FSMContext
) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    photo = message.photo[-1]
    if photo.file_size and photo.file_size > MAX_TELEGRAM_PHOTO_SIZE:
        await _delete_admin_input(message)
        data = await state.get_data()
        await _update_admin_prompt(
            message,
            state,
            tr(None, "series_photo_too_large_edit"),
            reply_markup=build_edit_series_photo_prompt(
                int(data.get("series_id", 0)),
                bool(data.get("has_photo")),
            ),
        )
        return
    data = await state.get_data()
    if not data.get("series_id"):
        await state.clear()
        await _delete_admin_input(message)
        await _answer_admin(message, tr(None, "series_edit_stale"))
        return
    prompt_message_id = data.get("photo_prompt_message_id")
    if isinstance(prompt_message_id, int):
        await message_service.safe_delete(
            message.bot,
            message.chat.id,
            prompt_message_id,
        )
    try:
        item = series_service.get_series_by_id(int(data["series_id"]))
    except (TypeError, ValueError, CatalogError):
        logger.exception("Unable to load selected series photo target.")
        await state.clear()
        await _delete_admin_input(message)
        await _answer_admin(
            message,
            tr(None, "series_edit_restart"),
        )
        return
    if not item:
        await state.clear()
        await _delete_admin_input(message)
        await _answer_admin(message, tr(None, "series_missing"))
        return
    await state.update_data(photo_file_id=photo.file_id)
    await state.set_state(AdminStates.waiting_edit_photo_confirmation)
    response = await message.answer_photo(
        photo=photo.file_id,
        caption=tr(
            None,
            "series_photo_new_preview",
            title=html.escape(str(item["title"])),
        ),
        reply_markup=build_admin_confirmation("admin_confirm_photo_edit"),
    )
    message_service.track_message(response.chat.id, response.message_id)
    await _delete_admin_input(message)


@router.message(AdminStates.waiting_edit_series_photo)
async def reject_non_photo_series_replacement(
    message: Message, state: FSMContext
) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    await _delete_admin_input(message)
    data = await state.get_data()
    await _update_admin_prompt(
        message,
        state,
        tr(None, "series_send_photo"),
        reply_markup=build_edit_series_photo_prompt(
            int(data.get("series_id", 0)),
            bool(data.get("has_photo")),
        ),
    )


@router.callback_query(F.data == "admin_confirm_photo_edit")
async def confirm_edit_series_photo(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    if await state.get_state() != AdminStates.waiting_edit_photo_confirmation.state:
        await replace_with_text(
            callback.message,
            tr(None, "series_preview_stale"),
            reply_markup=build_admin_menu(),
        )
        return
    data = await state.get_data()
    photo_url = data.get("photo_url")
    if not photo_url:
        try:
            photo_url = await _upload_telegram_photo(
                callback.bot,
                str(data["photo_file_id"]),
            )
        except (KeyError, ImageHostingError) as exc:
            logger.warning("Series photo replacement upload failed: %s", exc)
            await edit_text_or_caption(
                callback.message,
                tr(None, "series_upload_photo_error"),
                reply_markup=build_admin_confirmation("admin_confirm_photo_edit"),
            )
            return
        await state.update_data(photo_url=photo_url)
    try:
        item = series_service.set_series_photo(
            int(data["series_id"]),
            str(data["photo_file_id"]),
            str(photo_url),
        )
    except (KeyError, TypeError, ValueError, CatalogError) as exc:
        logger.exception("Could not update series photo.")
        await state.clear()
        await replace_with_text(
            callback.message,
            tr(None, "series_photo_save_error"),
            reply_markup=build_admin_menu(),
        )
        return
    await state.clear()
    await replace_with_text(
        callback.message,
        tr(None, "series_photo_updated", title=html.escape(str(item["title"]))),
        reply_markup=build_admin_menu(),
    )


@router.callback_query(F.data.startswith("admin_remove_series_photo:"))
async def request_remove_series_photo(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
        item = series_service.get_series_by_id(series_id)
    except (ValueError, CatalogError):
        logger.exception("Unable to load series for photo removal.")
        await edit_text_or_caption(callback.message, tr(None, "series_open_error"))
        return
    if (
        not item
        or not item.get("photo_file_id")
        or await state.get_state() != AdminStates.waiting_edit_series_photo.state
        or (await state.get_data()).get("series_id") != series_id
    ):
        await state.clear()
        await edit_text_or_caption(
            callback.message,
            tr(None, "series_remove_stale"),
            reply_markup=build_admin_menu(),
        )
        return
    await state.set_state(AdminStates.waiting_photo_remove_confirmation)
    await edit_text_or_caption(
        callback.message,
        tr(None, "series_remove_photo_question", title=html.escape(str(item["title"]))),
        reply_markup=build_confirm_remove_series_photo(series_id),
    )


@router.callback_query(F.data.startswith("admin_confirm_remove_series_photo:"))
async def confirm_remove_series_photo(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError):
        await edit_text_or_caption(callback.message, tr(None, "series_invalid_item"))
        return
    data = await state.get_data()
    if (
        await state.get_state() != AdminStates.waiting_photo_remove_confirmation.state
        or data.get("series_id") != series_id
    ):
        await state.clear()
        await edit_text_or_caption(
            callback.message,
            tr(None, "series_remove_photo_stale"),
            reply_markup=build_admin_menu(),
        )
        return
    try:
        item = series_service.set_series_photo(series_id, None)
    except (ValueError, CatalogError) as exc:
        logger.exception("Could not remove series photo.")
        await state.clear()
        await edit_text_or_caption(
            callback.message,
            tr(None, "series_remove_photo_error"),
            reply_markup=build_admin_menu(),
        )
        return
    await state.clear()
    await edit_text_or_caption(
        callback.message,
        tr(None, "series_photo_removed", title=html.escape(str(item["title"]))),
        reply_markup=build_admin_menu(),
    )


async def _preview_edit(message: Message, state: FSMContext, field: str) -> None:
    data = await state.get_data()
    series_id = data.get("series_id")
    value = (message.text or "").strip()
    await _delete_admin_input(message)
    if not value:
        await _update_admin_prompt(
            message,
            state,
            tr(None, "series_empty_value"),
        )
        return
    if field == "tiktok_url":
        try:
            SeriesService.validate_tiktok_url(value)
        except ValueError as exc:
            await _update_admin_prompt(
                message,
                state,
                tr(None, "series_invalid_tiktok"),
            )
            return
    try:
        item = series_service.get_series_by_id(int(series_id))
    except (TypeError, ValueError, CatalogError):
        logger.exception("Unable to load series while preparing edit.")
        item = None
    if not item:
        await _update_admin_prompt(
            message,
            state,
            tr(None, "series_edit_missing"),
        )
        await state.clear()
        return
    title = value if field == "title" else str(item["title"])
    url = value if field == "tiktok_url" else str(item["tiktok_url"])
    await state.update_data(edit_value=value, original_title=item["title"])
    await state.set_state(AdminStates.waiting_edit_confirmation)
    await _update_admin_prompt(
        message,
        state,
        tr(
            None,
            "series_edit_preview",
            title=html.escape(title),
            url=html.escape(url),
        ),
        reply_markup=build_admin_confirmation("admin_confirm_edit"),
    )


@router.message(AdminStates.waiting_edit_title)
async def receive_edit_title(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    await _preview_edit(message, state, "title")


@router.message(AdminStates.waiting_edit_url)
async def receive_edit_url(message: Message, state: FSMContext) -> None:
    if not await ensure_admin(message):
        await state.clear()
        return
    await _preview_edit(message, state, "tiktok_url")


@router.callback_query(F.data == "admin_confirm_edit")
async def confirm_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    if await state.get_state() != AdminStates.waiting_edit_confirmation.state:
        await replace_with_text(
            callback.message,
            tr(None, "series_preview_stale"),
            reply_markup=build_admin_menu(),
        )
        return
    data = await state.get_data()
    field = data.get("edit_field")
    value = data.get("edit_value")
    try:
        if field not in {"title", "tiktok_url"} or value is None:
            raise ValueError("Данные изменения неполные.")
        item = series_service.edit_series(
            int(data["series_id"]),
            **{field: str(value)},
        )
    except (KeyError, TypeError, ValueError, CatalogError) as exc:
        logger.info("Series edit rejected: %s", exc)
        await replace_with_text(
            callback.message,
            tr(None, "series_edit_save_error"),
            reply_markup=build_admin_menu(),
        )
        await state.clear()
        return
    await state.clear()
    await replace_with_text(
        callback.message,
        tr(None, "series_updated", title=html.escape(str(item["title"]))),
        reply_markup=build_admin_menu(),
    )


@router.callback_query(F.data.startswith("admin_select_delete:"))
async def select_delete(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
        item = series_service.get_series_by_id(series_id)
    except (ValueError, CatalogError):
        logger.exception("Unable to select series for deletion.")
        await replace_with_text(callback.message, tr(None, "series_open_error"))
        return
    if not item:
        await replace_with_text(
            callback.message,
            tr(None, "series_missing"),
            reply_markup=build_admin_menu(),
        )
        return
    await state.clear()
    await state.update_data(series_id=series_id)
    await state.set_state(AdminStates.waiting_delete_confirmation)
    await replace_with_text(
        callback.message,
        tr(None, "series_delete_question", title=html.escape(str(item["title"]))),
        reply_markup=build_delete_confirmation(series_id),
    )


@router.callback_query(F.data.startswith("admin_confirm_delete:"))
async def confirm_delete(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin(callback):
        return
    await callback.answer()
    try:
        series_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError):
        await replace_with_text(callback.message, tr(None, "series_invalid_item"))
        return
    data = await state.get_data()
    if (
        await state.get_state() != AdminStates.waiting_delete_confirmation.state
        or data.get("series_id") != series_id
    ):
        await replace_with_text(
            callback.message,
            tr(None, "series_delete_stale"),
            reply_markup=build_admin_menu(),
        )
        return
    try:
        deleted = series_service.delete_series(series_id)
    except CatalogError:
        logger.exception("Unable to delete selected series.")
        deleted = False
    await state.clear()
    if not deleted:
        await replace_with_text(
            callback.message,
            tr(None, "series_delete_error"),
            reply_markup=build_admin_menu(),
        )
        return
    await replace_with_text(
        callback.message,
        tr(None, "series_deleted"),
        reply_markup=build_admin_menu(),
    )
