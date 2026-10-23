from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, int]], Awaitable[None]]
BlockedCallback = Callable[[int], Awaitable[None]]


class BroadcastService:
    def __init__(self) -> None:
        self.is_running = False
        self.cancel_requested = False
        self._cancel_event = asyncio.Event()

    def cancel(self) -> None:
        if self.is_running:
            self.cancel_requested = True
            self._cancel_event.set()

    async def send_broadcast(
        self,
        bot: Bot,
        user_ids: list[int],
        text: str,
        *,
        progress: ProgressCallback | None = None,
        on_blocked: BlockedCallback | None = None,
    ) -> dict[str, int | bool]:
        if self.is_running:
            raise RuntimeError("Рассылка уже запущена.")

        self.is_running = True
        self.cancel_requested = False
        self._cancel_event.clear()
        success = 0
        failed = 0
        delivered = 0
        try:
            for user_id in user_ids:
                if self.cancel_requested:
                    break
                attempt_completed = False
                while True:
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=text,
                            parse_mode=None,
                        )
                        success += 1
                        attempt_completed = True
                        break
                    except TelegramRetryAfter as exc:
                        logger.info("Telegram requested broadcast retry after %s seconds.", exc.retry_after)
                        try:
                            await asyncio.wait_for(
                                self._cancel_event.wait(),
                                timeout=float(exc.retry_after) + 1,
                            )
                        except asyncio.TimeoutError:
                            pass
                        if self.cancel_requested:
                            break
                    except TelegramForbiddenError:
                        failed += 1
                        attempt_completed = True
                        if on_blocked:
                            await on_blocked(user_id)
                        break
                    except (TelegramAPIError, OSError, ValueError):
                        logger.warning(
                            "Broadcast delivery failed for recipient %s.",
                            user_id,
                            exc_info=True,
                        )
                        failed += 1
                        attempt_completed = True
                        break
                if attempt_completed:
                    delivered += 1
                if self.cancel_requested:
                    break
                if progress and (delivered % 10 == 0 or delivered == len(user_ids)):
                    try:
                        await progress(
                            {
                                "target": len(user_ids),
                                "processed": delivered,
                                "success": success,
                                "failed": failed,
                            }
                        )
                    except TelegramAPIError:
                        logger.warning("Could not update broadcast progress message.", exc_info=True)
                await asyncio.sleep(0.05)
        finally:
            cancelled = self.cancel_requested
            self.is_running = False
            self.cancel_requested = False

        return {
            "success": success,
            "failed": failed,
            "processed": delivered,
            "target": len(user_ids),
            "cancelled": cancelled,
        }
