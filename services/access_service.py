from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError

from config import settings

logger = logging.getLogger(__name__)


async def has_active_subscription(bot, user_id: int) -> bool:
    """Check live membership; an enabled but incomplete policy denies access."""
    if not settings.SUBSCRIPTION_REQUIRED:
        return True
    channel_id = settings.SUBSCRIPTION_CHANNEL_ID
    if channel_id in (None, ""):
        logger.error("Subscription is required but no channel is configured.")
        return False
    try:
        member = await bot.get_chat_member(channel_id, user_id)
    except TelegramAPIError as exc:
        logger.warning("Subscription check failed (%s).", type(exc).__name__)
        return False
    status = member.status
    return status in {"member", "administrator", "creator"} or (
        status == "restricted" and getattr(member, "is_member", False) is True
    )
