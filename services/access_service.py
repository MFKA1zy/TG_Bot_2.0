from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError

from config import settings
from services.i18n import normalize_language, tr, get_update_user

logger = logging.getLogger(__name__)
_INFLIGHT: dict[tuple, asyncio.Task] = {}


async def has_active_subscription(bot, user_id: int) -> bool:
    """Check live membership; an enabled but incomplete policy denies access."""
    if not settings.SUBSCRIPTION_REQUIRED:
        return True
    channel_id = settings.SUBSCRIPTION_CHANNEL_ID
    if channel_id in (None, ""):
        logger.error("Subscription is required but no channel is configured.")
        return False
    key = (id(bot), channel_id, user_id)
    task = _INFLIGHT.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_check_member(bot, channel_id, user_id))
        _INFLIGHT[key] = task
        def cleanup(completed):
            if _INFLIGHT.get(key) is completed:
                _INFLIGHT.pop(key, None)
        task.add_done_callback(cleanup)
    return await asyncio.shield(task)


async def _check_member(bot, channel_id, user_id) -> bool:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
    except TelegramAPIError as exc:
        logger.warning("Subscription check failed (%s).", type(exc).__name__)
        return False
    status = member.status
    return status in {"member", "administrator", "creator"} or (
        status == "restricted" and getattr(member, "is_member", False) is True
    )


async def authorize(event, repository=None, subscription_check=None) -> str | None:
    """Acknowledge callbacks before storage/network work; never cache access."""
    from aiogram.types import CallbackQuery
    from database.repository import UserRepository

    callback = isinstance(event, CallbackQuery)
    message = event.message if callback else event
    if callback:
        await event.answer()
    if not message or message.chat.type != "private":
        return None
    user = await get_update_user(event.from_user.id, repository)
    language = normalize_language(user.language if user else None)
    error = "access_blocked" if user and user.is_blocked else "inline_captcha_required" if not user or not user.language or not user.captcha_passed else None
    if error:
        await message.answer(tr(language, error))
        return None
    if not await (subscription_check or has_active_subscription)(event.bot, event.from_user.id):
        from handlers.start import show_subscription_required

        await show_subscription_required(event)
        return None
    return language
