from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.repository import UserRepository
from services.access_service import has_active_subscription
from services.catalog_service import CatalogService, SECTIONS
from services.i18n import normalize_language, tr
from services.series_cards import show_card
from services.series_service import CatalogError
from utils.telegram import replace_with_text

router = Router()
catalog = CatalogService()
users = UserRepository()


def back_button(builder, language, target="cat:home"):
    builder.button(text=tr(language, "back"), callback_data=target)


async def authorized(callback: CallbackQuery) -> str | None:
    user = await users.get_user_by_telegram_id(callback.from_user.id)
    language = normalize_language(user.language if user else None)
    if not callback.message or callback.message.chat.type != "private":
        await callback.answer()
        return None
    if user and user.is_blocked:
        await callback.answer(tr(language, "access_blocked"), show_alert=True)
        return None
    if not user or not user.language or not user.captcha_passed:
        await callback.answer(tr(language, "inline_captcha_required"), show_alert=True)
        return None
    if not await has_active_subscription(callback.bot, callback.from_user.id):
        from handlers.start import show_subscription_required

        await callback.answer()
        await show_subscription_required(callback)
        return None
    return language


def context(parts: list[str], start: int = 0) -> tuple[str, int, int]:
    section, page, category_id = parts[start:start + 3]
    if section not in SECTIONS:
        raise ValueError("section")
    return section, max(0, int(page)), max(0, int(category_id))


async def list_page(callback, language, section, page, category_id):
    result = await catalog.page(section, callback.from_user.id, page, category_id)
    builder = InlineKeyboardBuilder()
    suffix = f"{section}:{result.page}:{category_id}"
    for item in result.items:
        builder.button(text=item["title"][:60], callback_data=f"cat:card:{item['id']}:{suffix}")
    if result.page:
        builder.button(text="‹", callback_data=f"cat:list:{section}:{result.page - 1}:{category_id}")
    if result.page + 1 < result.pages:
        builder.button(text="›", callback_data=f"cat:list:{section}:{result.page + 1}:{category_id}")
    back_button(builder, language, "cat:categories:0" if section == "categories" else "cat:home")
    builder.adjust(1)
    text = f"<b>{tr(language, 'cat_' + section)}</b> · {result.page + 1}/{result.pages}"
    if not result.items:
        text += "\n\n" + tr(language, "cat_empty")
    await replace_with_text(callback.message, text, reply_markup=builder.as_markup())


async def card(callback, language, series_id, section, page, category_id):
    item = catalog.published(series_id)
    if not item:
        await list_page(callback, language, section, page, category_id)
        return
    favorite = series_id in await catalog.repository.favorite_ids(callback.from_user.id)
    watched = series_id in await catalog.repository.viewed_ids(callback.from_user.id)
    suffix = f"{section}:{page}:{category_id}"
    builder = InlineKeyboardBuilder()
    if item["episodes"]:
        builder.button(text=tr(language, "cat_watch"), callback_data=f"cat:watch:{series_id}:0:{suffix}")
    else:
        builder.button(text=tr(language, "cat_watch"), url=item["tiktok_url"])
    builder.button(text=tr(language, "cat_unfavorite" if favorite else "cat_favorite"), callback_data=f"cat:fav:{series_id}:{int(not favorite)}:{suffix}")
    if not watched:
        builder.button(text=tr(language, "cat_mark_viewed"), callback_data=f"cat:view:{series_id}:{suffix}")
    back_button(builder, language, f"cat:list:{suffix}")
    builder.adjust(1)
    counts = await catalog.repository.view_counts()
    await show_card(callback.message, item, await catalog.repository.categories(), language, builder.as_markup(), counts.get(series_id, (0, 0))[0])


@router.callback_query(F.data.startswith("cat:"))
async def catalog_callback(callback: CallbackQuery) -> None:
    language = await authorized(callback)
    if language is None:
        return
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]
    try:
        if action == "home":
            builder = InlineKeyboardBuilder()
            for section in SECTIONS:
                target = "cat:categories:0" if section == "categories" else f"cat:list:{section}:0:0"
                builder.button(text=tr(language, "cat_" + section), callback_data=target)
            back_button(builder, language, "main_menu")
            builder.adjust(1)
            await replace_with_text(callback.message, tr(language, "cat_title"), reply_markup=builder.as_markup())
        elif action == "categories":
            rows = await catalog.repository.categories()
            pages = max(1, (len(rows) + 7) // 8)
            page = min(max(0, int(parts[2])), pages - 1)
            builder = InlineKeyboardBuilder()
            for row in rows[page * 8:(page + 1) * 8]:
                builder.button(text=row["name"], callback_data=f"cat:list:categories:0:{row['id']}")
            if page:
                builder.button(text="‹", callback_data=f"cat:categories:{page - 1}")
            if page + 1 < pages:
                builder.button(text="›", callback_data=f"cat:categories:{page + 1}")
            back_button(builder, language)
            builder.adjust(1)
            text = tr(language, "cat_categories") if rows else tr(language, "cat_empty")
            await replace_with_text(callback.message, text, reply_markup=builder.as_markup())
        elif action == "list":
            await list_page(callback, language, *context(parts, 2))
        elif action == "card":
            await card(callback, language, int(parts[2]), *context(parts, 3))
        elif action == "fav":
            if parts[3] not in {"0", "1"}:
                raise ValueError("favorite")
            await catalog.set_favorite(callback.from_user.id, int(parts[2]), parts[3] == "1")
            await card(callback, language, int(parts[2]), *context(parts, 4))
        elif action == "view":
            if not catalog.published(int(parts[2])):
                raise ValueError("unavailable")
            section, page, category_id = context(parts, 3)
            builder = InlineKeyboardBuilder()
            builder.button(text=tr(language, "cat_confirm_view"), callback_data=f"cat:confirm:{parts[2]}:{section}:{page}:{category_id}")
            back_button(builder, language, f"cat:card:{parts[2]}:{section}:{page}:{category_id}")
            builder.adjust(1)
            await replace_with_text(callback.message, tr(language, "cat_view_notice"), reply_markup=builder.as_markup())
        elif action == "confirm":
            await catalog.confirm_view(callback.from_user.id, int(parts[2]))
            await card(callback, language, int(parts[2]), *context(parts, 3))
        elif action == "watch":
            item = catalog.published(int(parts[2]))
            if not item:
                raise ValueError("unavailable")
            section, page, category_id = context(parts, 4)
            episodes = item["episodes"]
            pages = max(1, (len(episodes) + 7) // 8)
            episode_page = min(max(0, int(parts[3])), pages - 1)
            suffix = f"{section}:{page}:{category_id}"
            builder = InlineKeyboardBuilder()
            builder.button(text=tr(language, "cat_watch"), url=item["tiktok_url"])
            for episode in episodes[episode_page * 8:(episode_page + 1) * 8]:
                builder.button(text=episode["title"], url=episode["url"])
            if episode_page:
                builder.button(text="‹", callback_data=f"cat:watch:{item['id']}:{episode_page - 1}:{suffix}")
            if episode_page + 1 < pages:
                builder.button(text="›", callback_data=f"cat:watch:{item['id']}:{episode_page + 1}:{suffix}")
            back_button(builder, language, f"cat:card:{item['id']}:{suffix}")
            builder.adjust(1)
            await replace_with_text(callback.message, tr(language, "cat_episodes"), reply_markup=builder.as_markup())
        else:
            raise ValueError("action")
    except (ValueError, IndexError, CatalogError):
        builder = InlineKeyboardBuilder()
        back_button(builder, language)
        await replace_with_text(callback.message, tr(language, "cat_unavailable"), reply_markup=builder.as_markup())
