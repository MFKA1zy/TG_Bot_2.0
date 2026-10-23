from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.reviews_repository import ReviewError
from handlers.catalog import context
from services.access_service import authorize
from services.i18n import tr
from services.reviews_service import ReviewsService
from utils.telegram import replace_with_text

router = Router()
service = ReviewsService()


class ReviewStates(StatesGroup):
    text = State()


def navigation(series_id, suffix, language):
    builder = InlineKeyboardBuilder()
    builder.button(text=tr(language, "back"), callback_data=f"rev:back:{series_id}:{suffix}")
    return builder


async def rating_screen(callback, series_id, suffix, language, saved=False, title=""):
    row = await service.repository.rating_summary(series_id, callback.from_user.id)
    builder = InlineKeyboardBuilder()
    for score in range(1, 11):
        builder.button(text=str(score), callback_data=f"rev:score:{series_id}:{score}:{suffix}")
    builder.adjust(5)
    builder.row(*navigation(series_id, suffix, language).buttons)
    text = f"<b>{html.escape(title[:200])}</b>\n" + (tr(language, "rv_saved") + "\n" if saved else "") + tr(language, "rv_rating_summary", average=f"{row['average']:.1f}", votes=row["votes"], own=row["own"] or "—")
    await replace_with_text(callback.message, text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("rev:"))
async def review_callback(callback: CallbackQuery, state: FSMContext):
    language = await authorize(callback)
    if language is None:
        return
    await state.clear()
    parts = callback.data.split(":")
    action = parts[1]
    try:
        # Offset of the navigation suffix varies by action; never contains a user ID.
        position = {"rate": 3, "score": 4, "list": 4, "write": 3, "own": 3, "back": 3,
                    "delete": 5, "delok": 5, "report": 4, "reason": 5}[action]
        section, page, category = context(parts, position)
        suffix = f"{section}:{page}:{category}"
        series_id = int(parts[4]) if action in {"delete", "delok", "reason"} else int(parts[3]) if action == "report" else int(parts[2])
        item = await service.require_series(series_id)
        if action == "back":
            await state.clear()
            from handlers.catalog import card

            await card(callback, language, series_id, section, page, category)
        elif action in {"rate", "score"}:
            if action == "score":
                await service.rate(callback.from_user.id, series_id, int(parts[3]), f"rating:{callback.id}")
            await rating_screen(callback, series_id, suffix, language, action == "score", item["title"])
        elif action == "write":
            await state.clear()
            await state.set_data({"series_id": series_id, "suffix": suffix})
            await state.set_state(ReviewStates.text)
            await replace_with_text(callback.message, tr(language, "rv_prompt"), reply_markup=navigation(series_id, suffix, language).as_markup())
        elif action == "own":
            row = await service.repository.own_review(callback.from_user.id, series_id)
            builder = navigation(series_id, suffix, language)
            builder.button(text=tr(language, "rv_write"), callback_data=f"rev:write:{series_id}:{suffix}")
            if row and row["status"] != "deleted":
                builder.button(text=tr(language, "rv_delete"), callback_data=f"rev:delete:{row['id']}:{row['version']}:{series_id}:{suffix}")
                text = tr(language, "rv_status_" + row["status"]) + "\n\n" + html.escape(row["text"])
            else:
                text = tr(language, "rv_no_own")
            builder.adjust(1)
            await replace_with_text(callback.message, text, reply_markup=builder.as_markup())
        elif action in {"delete", "delok"}:
            row = await service.repository.review(int(parts[2]))
            if not row or row["series_id"] != series_id or row["user_id"] != callback.from_user.id:
                raise ReviewError("missing")
            builder = navigation(series_id, suffix, language)
            if action == "delok":
                await service.repository.delete_own(callback.from_user.id, row["id"], int(parts[3]))
                text = tr(language, "rv_status_deleted")
            else:
                builder.button(text=tr(language, "admin_confirm"), callback_data=f"rev:delok:{row['id']}:{parts[3]}:{series_id}:{suffix}")
                text = tr(language, "rv_delete_confirm")
            builder.adjust(1)
            await replace_with_text(callback.message, text, reply_markup=builder.as_markup())
        elif action == "list":
            rows, review_page, pages = await service.repository.public_reviews(series_id, int(parts[3]))
            builder = InlineKeyboardBuilder()
            text = f"<b>{html.escape(item['title'][:200])}</b> · {review_page + 1}/{pages}\n\n"
            for row in rows:
                text += f"<b>{html.escape(row['author'][:60])}</b> · {row['score'] or '—'}/10 · {row['published_at'][:10]}\n{html.escape(row['text'])}"
                if row["user_id"] != callback.from_user.id:
                    builder.button(text=tr(language, "rv_report"), callback_data=f"rev:report:{row['id']}:{series_id}:{suffix}")
            if not rows:
                text += tr(language, "rv_empty")
            if review_page:
                builder.button(text="‹", callback_data=f"rev:list:{series_id}:{review_page - 1}:{suffix}")
            if review_page + 1 < pages:
                builder.button(text="›", callback_data=f"rev:list:{series_id}:{review_page + 1}:{suffix}")
            builder.button(text=tr(language, "rv_own"), callback_data=f"rev:own:{series_id}:{suffix}")
            builder.button(text=tr(language, "back"), callback_data=f"rev:back:{series_id}:{suffix}")
            builder.adjust(1)
            await replace_with_text(callback.message, text, reply_markup=builder.as_markup())
        elif action in {"report", "reason"}:
            review_id = int(parts[2])
            builder = navigation(series_id, suffix, language)
            if action == "reason":
                await service.report(callback.from_user.id, review_id, parts[3])
                text = tr(language, "rv_reported")
            else:
                row = await service.repository.review(review_id)
                if not row or row["series_id"] != series_id or row["status"] != "published" or row["user_id"] == callback.from_user.id:
                    raise ReviewError("invalid")
                for reason in ("abuse", "spam", "content", "other"):
                    builder.button(text=tr(language, "rv_reason_" + reason), callback_data=f"rev:reason:{review_id}:{reason}:{series_id}:{suffix}")
                text = tr(language, "rv_report_prompt")
            builder.adjust(1)
            await replace_with_text(callback.message, text, reply_markup=builder.as_markup())
    except (ReviewError, ValueError, KeyError, IndexError) as exc:
        code = str(exc) if isinstance(exc, ReviewError) else "invalid"
        builder = InlineKeyboardBuilder()
        builder.button(text=tr(language, "back"), callback_data="cat:home")
        await replace_with_text(callback.message, tr(language, "rv_error_" + code), reply_markup=builder.as_markup())


@router.message(ReviewStates.text)
async def review_text(message: Message, state: FSMContext):
    language = await authorize(message)
    if language is None:
        return
    data = await state.get_data()
    try:
        row = await service.submit(message.from_user.id, int(data["series_id"]), message.text or "", f"message:{message.chat.id}:{message.message_id}")
    except (ReviewError, KeyError) as exc:
        code = str(exc) if isinstance(exc, ReviewError) else "stale"
        await message.answer(tr(language, "rv_error_" + code))
        return
    await state.clear()
    await message.answer(tr(language, "rv_status_" + row["status"]), reply_markup=navigation(data["series_id"], data["suffix"], language).as_markup())
