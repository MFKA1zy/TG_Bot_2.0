from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.i18n import tr


def build_admin_menu(language: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(language, "admin_add_series"), callback_data="admin_add_series")
    builder.button(text=tr(language, "admin_catalog"), callback_data="admin_list_series")
    builder.button(text=tr(language, "admin_edit_series"), callback_data="admin_edit_series")
    builder.button(text=tr(language, "admin_delete_series"), callback_data="admin_delete_series")
    builder.button(text=tr(language, "admin_edit_welcome"), callback_data="admin_edit_welcome")
    builder.button(text=tr(language, "admin_broadcast"), callback_data="admin_broadcast")
    builder.button(text=tr(language, "admin_stats"), callback_data="admin_stats")
    builder.button(text=tr(language, "main_menu"), callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def build_admin_confirmation(
    confirm_data: str, language: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(language, "admin_confirm"), callback_data=confirm_data)
    builder.button(text=tr(language, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_welcome_language_menu(language: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in ("ru", "uk", "en"):
        builder.button(
            text=tr(language, f"language_{code}"),
            callback_data=f"admin_welcome_lang:{code}",
        )
    builder.button(text=tr(language, "main_menu"), callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()


def build_add_series_photo_prompt() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(None, "admin_no_photo"), callback_data="admin_skip_series_photo")
    builder.button(text=tr(None, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_admin_series_keyboard(
    items: list[dict], action: str, page: int = 0, page_size: int = 8
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = max(page, 0) * page_size
    page_items = items[start : start + page_size]
    callback_prefix = {
        "info": "admin_series_info",
        "edit": "admin_select_edit",
        "delete": "admin_select_delete",
    }.get(action, "admin_series_info")
    for item in page_items:
        builder.button(
            text=str(item.get("title", tr(None, "admin_untitled")))[:60],
            callback_data=f"{callback_prefix}:{item.get('id')}",
        )
    if page > 0:
        builder.button(text=tr(None, "back"), callback_data=f"admin_series_page:{action}:{page - 1}")
    if start + page_size < len(items):
        builder.button(text=tr(None, "next"), callback_data=f"admin_series_page:{action}:{page + 1}")
    builder.button(text=tr(None, "admin_panel_button"), callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()


def build_edit_series_fields(series_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(None, "admin_change_title"), callback_data=f"admin_edit_title:{series_id}")
    builder.button(text=tr(None, "admin_change_url"), callback_data=f"admin_edit_url:{series_id}")
    builder.button(text=tr(None, "admin_change_photo"), callback_data=f"admin_edit_photo:{series_id}")
    builder.button(text=tr(None, "admin_panel_button"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_edit_series_photo_prompt(
    series_id: int, has_photo: bool
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_photo:
        builder.button(
            text=tr(None, "admin_remove_photo"),
            callback_data=f"admin_remove_series_photo:{series_id}",
        )
    builder.button(text=tr(None, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_confirm_remove_series_photo(series_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=tr(None, "admin_remove_photo"),
        callback_data=f"admin_confirm_remove_series_photo:{series_id}",
    )
    builder.button(text=tr(None, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_delete_confirmation(series_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(None, "admin_delete_button"), callback_data=f"admin_confirm_delete:{series_id}")
    builder.button(text=tr(None, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_admin_series_actions(series_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(None, "admin_edit_series"), callback_data=f"admin_select_edit:{series_id}")
    builder.button(text=tr(None, "admin_delete_button"), callback_data=f"admin_select_delete:{series_id}")
    builder.button(text=tr(None, "admin_catalog"), callback_data="admin_list_series")
    builder.adjust(1)
    return builder.as_markup()


def build_broadcast_confirmation() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(None, "broadcast_start"), callback_data="admin_confirm_broadcast")
    builder.button(text=tr(None, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_broadcast_running() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(None, "broadcast_cancel"), callback_data="admin_cancel_broadcast")
    return builder.as_markup()


def build_broadcast_recovery() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=tr(None, "broadcast_recover"),
        callback_data="admin_recover_broadcast",
    )
    builder.button(text=tr(None, "admin_panel_button"), callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()
