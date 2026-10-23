from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.i18n import SUPPORTED_LANGUAGES, tr


def build_language_menu(language: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGUAGES:
        builder.button(
            text=tr(language, f"language_{code}"),
            callback_data=f"set_language:{code}",
        )
    builder.adjust(1)
    return builder.as_markup()


def build_main_menu(language: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=tr(language, "search"),
        switch_inline_query_current_chat="",
    )
    builder.button(
        text=tr(language, "settings"),
        callback_data="user_settings",
    )
    builder.adjust(1)
    return builder.as_markup()


def build_settings_menu(language: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=tr(language, "change_language"),
        callback_data="change_language",
    )
    builder.button(text=tr(language, "back"), callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()
