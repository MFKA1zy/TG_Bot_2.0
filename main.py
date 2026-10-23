from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.types import ErrorEvent

from config import settings
from database.connection import init_db
from services.i18n import LocaleMiddleware
from services.i18n import tr
from services.access_middleware import BlockedUserMiddleware


def configure_logging() -> None:
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(settings.LOGS_DIR / "bot.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


async def main() -> None:
    from services.storage import prepare_storage

    prepare_storage()
    from handlers import captcha as captcha_handlers
    from handlers import inline_search as inline_search_handlers
    from handlers import start as start_handlers
    from handlers.admin import admin_router

    configure_logging()
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Fill in the .env file before starting the bot.")

    await init_db()

    bot_session = AiohttpSession(
        proxy=settings.TELEGRAM_PROXY or None,
        timeout=settings.TELEGRAM_REQUEST_TIMEOUT,
    )
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=bot_session,
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(LocaleMiddleware())
    dp.update.outer_middleware(BlockedUserMiddleware())

    @dp.errors()
    async def handle_update_error(event: ErrorEvent) -> bool:
        error = event.exception
        details = str(error)
        if settings.BOT_TOKEN:
            details = details.replace(settings.BOT_TOKEN, "[REDACTED]")
        if isinstance(error, TelegramNetworkError):
            logging.getLogger(__name__).error(
                "Cannot reach Telegram API. Check internet access, DNS/firewall, "
                "and TELEGRAM_PROXY configuration. Details: %s",
                details,
            )
            return True
        logging.getLogger(__name__).error(
            "Unhandled error while processing an update (%s): %s",
            type(error).__name__,
            details,
        )
        update = event.update
        message = update.message or update.edited_message
        if message is not None:
            try:
                await message.answer(tr(None, "technical_error"))
            except TelegramAPIError:
                logging.getLogger(__name__).warning(
                    "Could not notify the user about an update error.",
                    exc_info=True,
                )
        elif update.callback_query is not None:
            try:
                await update.callback_query.answer(
                    tr(None, "technical_error"),
                    show_alert=True,
                )
            except TelegramAPIError:
                logging.getLogger(__name__).warning(
                    "Could not notify the user about a callback error.",
                    exc_info=True,
                )
        return True

    dp.include_router(start_handlers.router)
    dp.include_router(captcha_handlers.router)
    dp.include_router(inline_search_handlers.router)
    dp.include_router(admin_router)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
