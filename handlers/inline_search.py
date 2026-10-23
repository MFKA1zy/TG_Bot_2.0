from __future__ import annotations

import asyncio

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultsButton,
    InlineQueryResult,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LinkPreviewOptions,
)

from config import settings
from services.access_service import has_active_subscription
from database.repository import UserRepository
from database.catalog_repository import CatalogRepository
from services.series_cards import render_card
from services.i18n import detect_language, normalize_language, tr
from services.i18n import get_update_user
from services.series_service import CatalogError, SeriesService

logger = logging.getLogger(__name__)
router = Router()
series_service = SeriesService()
repository = UserRepository()
INLINE_RESULTS_LIMIT = 50


def _format_inline_message(item: dict, language: str = "ru", categories=None) -> str:
    url = html.escape(str(item["tiktok_url"]), quote=True)
    photo_url = item.get("photo_url")
    poster_link = (
        f'\n<a href="{html.escape(str(photo_url), quote=True)}">{tr(language, "inline_poster")}</a>'
        if photo_url
        else ""
    )
    return (
        render_card(item, categories or [], language) + "\n\n"
        f'<a href="{url}">{tr(language, "inline_tiktok")}</a>'
        f"{poster_link}"
    )


def build_inline_results(
    items: list[dict], language: str = "ru", categories=None
) -> list[InlineQueryResult]:
    results: list[InlineQueryResult] = []
    for item in items[:INLINE_RESULTS_LIMIT]:
        if item.get("publication_status", "published") != "published":
            continue
        title = str(item["title"])
        message_text = _format_inline_message(item, language, categories)
        photo_url = item.get("photo_url")
        link_preview_options = (
            LinkPreviewOptions(
                url=str(photo_url),
                prefer_large_media=True,
                show_above_text=True,
            )
            if photo_url
            else None
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=tr(language, "inline_button_tiktok"),
                        url=str(item["tiktok_url"]),
                    )
                ]
            ]
        )
        results.append(
            InlineQueryResultArticle(
                id=str(item["id"]),
                title=title[:256],
                description=(
                    tr(language, "inline_description_photo")
                    if photo_url
                    else tr(language, "inline_description_missing")
                    if item.get("photo_file_id")
                    else tr(language, "inline_description_default")
                ),
                thumbnail_url=str(photo_url) if photo_url else None,
                input_message_content=InputTextMessageContent(
                    message_text=message_text,
                    parse_mode=ParseMode.HTML,
                    link_preview_options=link_preview_options,
                ),
                reply_markup=markup,
            )
        )
    return results


def _info_result(text: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id="info",
        title=text[:256],
        input_message_content=InputTextMessageContent(message_text=text),
    )


@router.inline_query()
async def search_inline(inline_query: InlineQuery) -> None:
    telegram_language = detect_language(inline_query.from_user.language_code)
    user = await get_update_user(inline_query.from_user.id, repository)
    language = normalize_language(
        user.language
        if user and user.language
        else telegram_language
    )

    if user and getattr(user, "is_blocked", False):
        await inline_query.answer(results=[], cache_time=0, is_personal=True)
        return

    if not user or not user.captcha_passed or not user.language:
        text = tr(language, "inline_captcha_required")
        await inline_query.answer(
            results=[],
            button=InlineQueryResultsButton(text=text[:64], start_parameter="register"),
            cache_time=0,
            is_personal=True,
        )
        return
    if settings.SUBSCRIPTION_REQUIRED and not await has_active_subscription(inline_query.bot, inline_query.from_user.id):
        await inline_query.answer(
            results=[],
            button=InlineQueryResultsButton(text=tr(language, "subscription_check")[:64], start_parameter="subscription"),
            cache_time=0,
            is_personal=True,
        )
        return

    query = inline_query.query.strip()
    try:
        items = (
            await asyncio.to_thread(series_service.search_series, query)
            if query
            else await asyncio.to_thread(series_service.list_series)
        )
    except CatalogError:
        logger.exception("Series catalog could not be searched inline.")
        await inline_query.answer(results=[], cache_time=0, is_personal=True)
        return

    items = [item for item in items if item.get("publication_status", "published") == "published"]
    try:
        raw_offset = getattr(inline_query, "offset", "") or "0"
        if not raw_offset.isdigit() or len(raw_offset) > 9:
            raise ValueError("offset")
        offset = int(raw_offset)
    except ValueError:
        await inline_query.answer(results=[], cache_time=0, is_personal=True)
        return
    page_items = items[offset:offset + INLINE_RESULTS_LIMIT]
    categories = await CatalogRepository().categories() if any(item.get("category_ids") for item in page_items) else []
    results = build_inline_results(page_items, language, categories)
    if not results and not offset:
        results = [_info_result(tr(language, "inline_no_results"))]
    await inline_query.answer(
        results=results,
        cache_time=0,
        is_personal=True,
        next_offset=str(offset + INLINE_RESULTS_LIMIT) if offset + INLINE_RESULTS_LIMIT < len(items) else "",
    )
