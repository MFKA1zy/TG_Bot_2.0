from __future__ import annotations

import asyncio

import html

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, Message

from services.i18n import tr
from services.message_service import message_service
from utils.telegram import replace_with_text

_POSTER_IDS: dict[tuple, str] = {}

def _clip(text: str, limit: int) -> str:
    raw = text.encode("utf-16-le")
    if len(raw) <= limit * 2:
        return text
    return raw[:(limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def render_card(item: dict, categories: list[dict], language: str, views: int = 0) -> str:
    names = _clip(" / ".join(row["name"] for row in categories if row["id"] in item.get("category_ids", [])), 100)
    return (
        f"🎬 <b>{html.escape(_clip(item['title'], 200))}</b>\n\n"
        f"{tr(language, 'cat_genres')}: {html.escape(names) or '—'}\n"
        f"{tr(language, 'cat_episode_count')}: {item.get('episode_count') or '—'}\n"
        f"{tr(language, 'cat_status')}: {tr(language, 'cat_status_' + item.get('release_status', 'ongoing'))}\n"
        f"{tr(language, 'cat_views')}: {views}\n\n"
        + (tr(language, "rv_card_rating", average=f"{item.get('average', 0):.1f}", votes=item.get("votes", 0)) + "\n\n" if "average" in item else "")
        + f"{html.escape(_clip(item.get('description', ''), 430))}"
    ).strip()


async def show_card(message: Message, item: dict, categories: list[dict], language: str, markup: InlineKeyboardMarkup, views: int = 0) -> None:
    text = render_card(item, categories, language, views)
    key = (message.bot.id, item.get("photo_url") or item.get("photo_file_id"))
    candidates = list(dict.fromkeys(photo for photo in (_POSTER_IDS.get(key), item.get("photo_file_id"), item.get("photo_url")) if photo))
    response = None
    for photo in candidates:
        try:
            response = await message.answer_photo(photo, caption=text, reply_markup=markup)
        except TelegramAPIError:
            continue
        if response.photo:
            if len(_POSTER_IDS) >= 256:
                _POSTER_IDS.clear()
            _POSTER_IDS[key] = response.photo[-1].file_id
        await message_service.safe_delete(message.bot, message.chat.id, message.message_id)
        break
    if response is None:
        response = await replace_with_text(message, text, reply_markup=markup)
    await asyncio.to_thread(message_service.track_message, response.chat.id, response.message_id)
