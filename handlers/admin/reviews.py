from __future__ import annotations

import html
import json
import logging

import aiosqlite
from aiogram.exceptions import TelegramAPIError

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.admin.panel import ensure_admin
from services.i18n import tr
from services.moderation_service import normalize
from services.reviews_service import ReviewsService
from database.reviews_repository import ReviewError
from utils.telegram import replace_with_text

router = Router()
service = ReviewsService()
logger = logging.getLogger(__name__)


async def notify_warning(bot, user_id, reason):
    try:
        async with service.repository.connection() as db:
            user = await service.repository.one(db, "SELECT language FROM users WHERE telegram_id=?", (user_id,))
        await bot.send_message(
            user_id, tr(user["language"] or "ru", "rv_warning_received", text=html.escape(reason)),
            parse_mode="HTML",
        )
    except (TelegramAPIError, OSError, aiosqlite.Error) as exc:
        logger.warning("Warning notification delivery failed (%s).", type(exc).__name__)
        return False
    return True


class ModerationStates(StatesGroup):
    input = State()
    confirm = State()


def controls(confirm=False):
    builder = InlineKeyboardBuilder()
    if confirm:
        builder.button(text=tr(None, "admin_confirm"), callback_data="mod:commit")
    builder.button(text=tr(None, "back"), callback_data="mod:home")
    builder.button(text=tr(None, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


async def show_queue(callback, section, page=0, search=""):
    rows, page, pages = await service.repository.admin_page(section, page, search)
    builder = InlineKeyboardBuilder()
    for row in rows:
        target = "report" if section == "reports" else "review"
        label = f"#{row['id']} · " + (tr(None, "rv_reason_" + row["reason"]) if target == "report" else row["text"][:40])
        builder.button(text=label, callback_data=f"mod:{target}:{row['id']}")
    if page:
        builder.button(text="‹", callback_data=f"mod:queue:{section}:{page - 1}")
    if page + 1 < pages:
        builder.button(text="›", callback_data=f"mod:queue:{section}:{page + 1}")
    builder.button(text=tr(None, "back"), callback_data="mod:home")
    builder.adjust(1)
    await replace_with_text(callback.message, tr(None, "rv_admin_" + section) + f" · {page + 1}/{pages}" + ("\n" + tr(None, "rv_empty") if not rows else ""), reply_markup=builder.as_markup())


async def show_review(callback, review_id):
    row = await service.repository.review(review_id)
    if not row:
        raise ReviewError("missing")
    import asyncio
    item = await asyncio.to_thread(service.catalog.series.get_series_by_id, row["series_id"])
    title = item["title"] if item else str(row["series_id"])
    reasons = []
    for reason in json.loads(row["reasons"]):
        code, _, phrase = reason.partition(":")
        reasons.append(tr(None, "rv_filter_" + code) + (": " + phrase if phrase else ""))
    text = f"#{row['id']} · {html.escape(title[:200])}\n{html.escape(row['author'][:60])} · ID {row['user_id']}\n{tr(None, 'rv_admin_' + row['status'])}\n\n{html.escape(row['text'])}\n\n{html.escape('; '.join(reasons)[:400])}"
    builder = InlineKeyboardBuilder()
    for action in ("published", "rejected", "deleted"):
        if action == "published" and row["status"] == "published":
            continue
        builder.button(text=tr(None, "rv_action_" + action), callback_data=f"mod:act:{action}:{row['id']}:{row['version']}:0")
    builder.button(text=tr(None, "rv_author"), callback_data=f"mod:user:{row['user_id']}")
    builder.button(text=tr(None, "rv_series"), callback_data=f"cat:card:{row['series_id']}:all:0:0")
    builder.button(text=tr(None, "rv_rating_delete"), callback_data=f"mod:act:rating_delete:{row['user_id']}:0:{row['series_id']}")
    builder.button(text=tr(None, "back"), callback_data="mod:home")
    builder.adjust(1)
    await replace_with_text(callback.message, text, reply_markup=builder.as_markup())


async def show_user(callback, user_id, page=0):
    user = await service.repository.user_history(user_id, page)
    text = f"ID {user_id} · {html.escape(str(user['first_name'] or user['username'] or '—'))}\n" + tr(None, "rv_ban_state", status=int(user["is_blocked"]))
    builder = InlineKeyboardBuilder()
    for action in ("warn", "ban", "unban"):
        builder.button(text=tr(None, "rv_action_" + action), callback_data=f"mod:act:{action}:{user_id}:0:0")
    text += "\n\n" + tr(None, "rv_warnings") + "\n"
    for warning in user["warnings"]:
        text += f"#{warning['id']} · {warning['created_at'][:10]} · {html.escape(warning['reason'][:80])}" + (" ✓" if warning["revoked_at"] else "") + "\n"
        if not warning["revoked_at"]:
            builder.button(text=tr(None, "rv_action_revoke") + f" #{warning['id']}", callback_data=f"mod:act:revoke:{warning['id']}:0:0")
    text += "\n" + tr(None, "rv_history") + "\n"
    event_keys = {"submit": "rv_write", "edit": "rv_write", "author_delete": "rv_action_deleted",
                  "configure": "rv_admin_settings", "word_add": "rv_wordadd", "word_remove": "rv_wordremove",
                  "auto_ban": "rv_setting_auto_ban", "rating_delete": "rv_rating_delete"}
    for entry in user["events"]:
        label = tr(None, event_keys.get(entry["action"], "rv_action_" + entry["action"]))
        text += f"{entry['created_at'][:10]} · {label} · {tr(None, 'rv_actor')} {entry['actor_id']} · {html.escape(entry['reason'][:60])}\n"
    for rating in user["ratings"]:
        builder.button(text=tr(None, "rv_rating_delete") + f" · #{rating['series_id']} ({rating['score']}/10)", callback_data=f"mod:act:rating_delete:{user_id}:0:{rating['series_id']}")
    if user["page"]:
        builder.button(text="‹", callback_data=f"mod:user:{user_id}:{user['page'] - 1}")
    if user["page"] + 1 < user["pages"]:
        builder.button(text="›", callback_data=f"mod:user:{user_id}:{user['page'] + 1}")
    builder.button(text=tr(None, "back"), callback_data="mod:home")
    builder.adjust(1)
    await replace_with_text(callback.message, text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("mod:"))
async def moderation_callback(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin(callback):
        return
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]
    try:
        if action == "home":
            await state.clear()
            builder = InlineKeyboardBuilder()
            for section in ("pending", "published", "rejected", "deleted", "reports"):
                builder.button(text=tr(None, "rv_admin_" + section), callback_data=f"mod:queue:{section}:0")
            for section in ("search", "userlookup", "settings", "words", "stats"):
                builder.button(text=tr(None, "rv_admin_" + section), callback_data=f"mod:{section}")
            builder.button(text=tr(None, "back"), callback_data="admin_panel")
            builder.adjust(1)
            await replace_with_text(callback.message, tr(None, "rv_admin"), reply_markup=builder.as_markup())
        elif action == "queue":
            data = await state.get_data()
            await state.clear()
            await state.set_data({"search": data.get("search", "")})
            await show_queue(callback, parts[2], int(parts[3]), data.get("search", ""))
        elif action == "review":
            await show_review(callback, int(parts[2]))
        elif action == "user":
            await show_user(callback, int(parts[2]), int(parts[3]) if len(parts) > 3 else 0)
        elif action == "report":
            async with service.repository.connection() as db:
                row = await service.repository.one(db, "SELECT * FROM review_reports WHERE id=?", (int(parts[2]),))
            if not row:
                raise ReviewError("missing")
            builder = InlineKeyboardBuilder()
            builder.button(text=tr(None, "rv_read"), callback_data=f"mod:review:{row['review_id']}")
            builder.button(text=tr(None, "rv_action_dismiss"), callback_data=f"mod:act:dismiss:{row['id']}:0:0")
            builder.button(text=tr(None, "back"), callback_data="mod:queue:reports:0")
            builder.adjust(1)
            await replace_with_text(callback.message, f"#{row['id']} · " + tr(None, "rv_reason_" + row["reason"]), reply_markup=builder.as_markup())
        elif action in {"search", "userlookup", "wordadd", "wordremove", "setting", "act"}:
            await state.clear()
            data = {"kind": action}
            if action == "act":
                if parts[2] not in {"published", "rejected", "deleted", "warn", "ban", "unban", "revoke", "dismiss", "rating_delete"}:
                    raise ReviewError("invalid")
                data.update(action=parts[2], target=int(parts[3]), version=int(parts[4]), extra=int(parts[5]))
                if data["action"] == "published":
                    await service.admin_action(
                        callback.from_user.id, "published", data["target"],
                        tr(None, "rv_approval_reason"),
                        f"approval:{callback.from_user.id}:{data['target']}:{data['version']}",
                        data["version"], data["extra"],
                    )
                    await show_review(callback, data["target"])
                    return
                text = tr(None, "rv_moderator_reason")
            elif action == "setting":
                if parts[2] not in {"auto_safe", "auto_ban", "forbidden_action", "warning_threshold", "min_votes", "review_cooldown"}:
                    raise ReviewError("invalid")
                data["key"] = parts[2]
                text = tr(None, "rv_prompt_" + parts[2])
            else:
                text = tr(None, "rv_prompt_" + action)
            await state.set_data(data)
            await state.set_state(ModerationStates.input)
            await replace_with_text(callback.message, text, reply_markup=controls())
        elif action == "commit":
            data = await state.get_data()
            if await state.get_state() != ModerationStates.confirm.state or data.get("prompt_id") != callback.message.message_id:
                raise ReviewError("stale")
            kind = data["kind"]
            notification = None
            if kind == "act":
                created = await service.admin_action(callback.from_user.id, data["action"], data["target"], data["reason"], f"moderation:{callback.from_user.id}:{callback.message.chat.id}:{callback.message.message_id}", data["version"], data["extra"])
                if created and data["action"] == "warn":
                    notification = (data["target"], data["reason"])
            elif kind == "setting":
                await service.repository.configure(callback.from_user.id, {data["key"]: data["value"]})
            elif kind in {"wordadd", "wordremove"}:
                await service.repository.change_word(callback.from_user.id, data["phrase"], kind == "wordadd")
            else:
                raise ReviewError("invalid")
            await state.clear()
            text = tr(None, "rv_saved")
            if notification:
                delivered = await notify_warning(callback.bot, *notification)
                text += "\n" + tr(None, "rv_warning_delivered" if delivered else "rv_warning_delivery_failed")
            await replace_with_text(callback.message, text, reply_markup=controls())
        elif action == "settings":
            policy = await service.repository.policy()
            builder = InlineKeyboardBuilder()
            for key, value in policy.items():
                display = tr(None, "rv_on" if value else "rv_off") if type(value) is bool else tr(None, "rv_admin_" + value) if key == "forbidden_action" else str(value)
                builder.button(text=tr(None, "rv_setting_" + key) + ": " + display, callback_data=f"mod:setting:{key}")
            builder.button(text=tr(None, "back"), callback_data="mod:home")
            builder.adjust(1)
            await replace_with_text(callback.message, tr(None, "rv_admin_settings"), reply_markup=builder.as_markup())
        elif action == "words":
            words = await service.repository.words()
            pages = max(1, (len(words) + 9) // 10)
            page = min(max(0, int(parts[2]) if len(parts) > 2 else 0), pages - 1)
            builder = InlineKeyboardBuilder()
            for kind in ("wordadd", "wordremove"):
                builder.button(text=tr(None, "rv_" + kind), callback_data="mod:" + kind)
            if page:
                builder.button(text="‹", callback_data=f"mod:words:{page - 1}")
            if page + 1 < pages:
                builder.button(text="›", callback_data=f"mod:words:{page + 1}")
            builder.button(text=tr(None, "back"), callback_data="mod:home")
            builder.adjust(1)
            await replace_with_text(callback.message, tr(None, "rv_admin_words") + f" · {page + 1}/{pages}\n\n" + html.escape("\n".join(words[page * 10:(page + 1) * 10])), reply_markup=builder.as_markup())
        elif action == "stats":
            row = await service.repository.stats()
            await replace_with_text(callback.message, tr(None, "rv_stats", **row), reply_markup=controls())
        else:
            raise ReviewError("invalid")
    except (ReviewError, ValueError, KeyError, IndexError) as exc:
        await state.clear()
        code = str(exc) if isinstance(exc, ReviewError) else "invalid"
        await replace_with_text(callback.message, tr(None, "rv_error_" + code), reply_markup=controls())


@router.message(ModerationStates.input)
async def moderation_input(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        await state.clear()
        return
    data = await state.get_data()
    text = (message.text or "").strip()
    try:
        if data["kind"] == "search":
            if not 1 <= len(text) <= 100:
                raise ReviewError("invalid")
            await state.clear()
            await state.set_data({"search": text})
            rows, _, _ = await service.repository.admin_page("search", 0, text)
            builder = InlineKeyboardBuilder()
            for row in rows:
                builder.button(text=f"#{row['id']} · {row['text'][:40]}", callback_data=f"mod:review:{row['id']}")
            builder.button(text=tr(None, "rv_more"), callback_data="mod:queue:search:0")
            builder.button(text=tr(None, "back"), callback_data="mod:home")
            builder.adjust(1)
            await message.answer(tr(None, "rv_admin_search") if rows else tr(None, "rv_empty"), reply_markup=builder.as_markup())
            return
        if data["kind"] == "userlookup":
            user = await service.repository.user_history(int(text))
            builder = InlineKeyboardBuilder()
            builder.button(text=tr(None, "rv_author"), callback_data=f"mod:user:{user['telegram_id']}")
            await message.answer(tr(None, "rv_author"), reply_markup=builder.as_markup())
            await state.clear()
            return
        if data["kind"] == "act":
            if not 1 <= len(text) <= 500:
                raise ReviewError("invalid")
            data["reason"] = text
        elif data["kind"] in {"wordadd", "wordremove"}:
            if not 1 <= len(text) <= 100:
                raise ReviewError("invalid")
            data["phrase"] = normalize(text)
        elif data["kind"] == "setting":
            key = data["key"]
            if key in {"auto_safe", "auto_ban"}:
                if text not in {"0", "1"}:
                    raise ReviewError("invalid")
                value = text == "1"
            elif key == "forbidden_action":
                if text not in {"pending", "rejected"}:
                    raise ReviewError("invalid")
                value = text
            else:
                value = int(text)
                if not 1 <= value <= 10000:
                    raise ReviewError("invalid")
            data["value"] = value
        response = await message.answer(tr(None, "cat_confirm_changes") + "\n\n" + html.escape(text), reply_markup=controls(True))
        await state.set_data({**data, "prompt_id": response.message_id})
        await state.set_state(ModerationStates.confirm)
    except (ValueError, KeyError, ReviewError):
        await message.answer(tr(None, "rv_error_invalid"), reply_markup=controls())
