from __future__ import annotations

import asyncio

from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message

from services.message_service import message_service


async def _track(message: Message) -> Message:
    await asyncio.to_thread(message_service.track_message, message.chat.id, message.message_id)
    return message


async def edit_tracked_text(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    try:
        response = await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if not any(
            reason in error_text
            for reason in ("message to edit not found", "message can't be edited")
        ):
            raise
        response = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
    return await _track(response)


async def replace_with_text(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    if message.photo:
        try:
            await message.delete()
        except TelegramBadRequest as exc:
            if "message to delete not found" in str(exc).lower():
                return await _track(
                    await message.answer(text, reply_markup=reply_markup)
                )
            return await _track(
                await message.edit_caption(
                    caption=text,
                    reply_markup=reply_markup,
                )
            )
        return await _track(await message.answer(text, reply_markup=reply_markup))
    try:
        response = await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
        return await _track(message)
    return await _track(response)


async def edit_text_or_caption(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    if message.photo:
        return await _track(
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
            )
        )
    return await _track(await message.edit_text(text, reply_markup=reply_markup))
