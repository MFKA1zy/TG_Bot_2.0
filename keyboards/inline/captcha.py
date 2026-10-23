from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.i18n import tr


def build_cancel_button(language: str | None = None) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(language, "admin_cancel"),
                    callback_data="cancel_action",
                )
            ]
        ]
    )
    return keyboard


def build_retry_captcha_button(language: str | None = None) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=tr(language, "captcha_retry"),
            callback_data="restart_captcha",
        )
    ]])
    return keyboard
