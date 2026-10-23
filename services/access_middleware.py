from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineQuery, Message

from database.repository import UserRepository
from services.i18n import tr


class BlockedUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        nested = event.event
        sender = getattr(nested, "from_user", None)
        if sender is None:
            return await handler(event, data)
        user = await UserRepository().get_user_by_telegram_id(sender.id)
        if not user or not user.is_blocked:
            return await handler(event, data)
        text = tr(user.language, "access_blocked")
        if isinstance(nested, InlineQuery):
            await nested.answer(results=[], cache_time=0, is_personal=True)
        elif isinstance(nested, CallbackQuery):
            await nested.answer(text, show_alert=True)
        elif isinstance(nested, Message) and nested.chat.type == "private":
            await nested.answer(text)
        return None
