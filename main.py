from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from services.fsm_storage import PruningMemoryStorage
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
    from services.logging_security import SecretFormatter

    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        RotatingFileHandler(settings.LOGS_DIR / "bot.log", maxBytes=5*1024*1024,
                            backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    formatter = SecretFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        secrets=(settings.BOT_TOKEN, settings.IMGBB_API_KEY,
                 settings.ADMIN_ACTIVATION_CODE, settings.TELEGRAM_PROXY),
    )
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )


async def main() -> None:
    from services.storage import prepare_storage

    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Fill in the .env file before starting the bot.")
    await asyncio.to_thread(prepare_storage)
    from services.series_service import SeriesService

    startup_catalog = await asyncio.to_thread(SeriesService)
    await asyncio.to_thread(startup_catalog.migrate_catalog)
    from handlers import captcha as captcha_handlers
    from handlers import inline_search as inline_search_handlers
    from handlers import start as start_handlers
    from handlers.admin import admin_router
    from handlers.catalog import router as catalog_router
    from handlers.reviews import router as reviews_router
    from handlers.profile import router as profile_router
    from handlers.roulette import router as roulette_router

    configure_logging()
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
    dp = Dispatcher(storage=PruningMemoryStorage())
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
    dp.include_router(catalog_router)
    dp.include_router(reviews_router)
    dp.include_router(profile_router)
    dp.include_router(roulette_router)
    dp.include_router(admin_router)

    async def shutdown_workers():
        import handlers.admin.broadcast as broadcast
        from services.lifecycle import shutdown_workers as drain

        await drain(dp,broadcast)

    dp.shutdown.register(shutdown_workers)

    try:
        await dp.start_polling(bot, close_bot_session=False)
    finally:
        await dp.storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
