from __future__ import annotations

import html
import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.admin.panel import ensure_admin
from services.catalog_service import CatalogService
from services.i18n import tr
from services.series_metadata import FIELDS, validate_metadata
from services.series_service import CatalogError
from utils.telegram import replace_with_text

router = Router()
catalog = CatalogService()


class CatalogAdminStates(StatesGroup):
    input = State()
    confirm = State()


def controls(back="admin_panel", confirm=False):
    builder = InlineKeyboardBuilder()
    if confirm:
        builder.button(text=tr(None, "admin_confirm"), callback_data="acat:save")
    builder.button(text=tr(None, "back"), callback_data=back)
    builder.button(text=tr(None, "admin_cancel"), callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def parse_field(field: str, text: str):
    text = text.strip()
    if field in {"alternative_titles", "category_ids"}:
        tokens = [] if text == "-" else [token.strip() for token in text.split(",") if token.strip()]
        return [int(token) for token in tokens] if field == "category_ids" else tokens
    if field == "episode_count":
        return int(text)
    if field == "episodes":
        if text == "-":
            return []
        rows = [line.split("|", 1) for line in text.splitlines()]
        if any(len(row) != 2 for row in rows):
            raise ValueError("episode format")
        return [{"title": title.strip(), "url": url.strip()} for title, url in rows]
    if field == "description":
        return "" if text == "-" else text
    if field == "recommended":
        if text not in {"0", "1"}:
            raise ValueError("boolean")
        return text == "1"
    return text


async def category_list(callback, page=0):
    rows = await catalog.repository.categories()
    pages = max(1, (len(rows) + 7) // 8)
    page = min(max(0, page), pages - 1)
    builder = InlineKeyboardBuilder()
    for row in rows[page * 8:(page + 1) * 8]:
        builder.button(text=f"{row['position']}. {row['name']}", callback_data=f"acat:category:{row['id']}")
    if page:
        builder.button(text="‹", callback_data=f"acat:categories:{page - 1}")
    if page + 1 < pages:
        builder.button(text="›", callback_data=f"acat:categories:{page + 1}")
    builder.button(text=tr(None, "cat_add_category"), callback_data="acat:addcategory")
    builder.button(text=tr(None, "back"), callback_data="admin_panel")
    builder.adjust(1)
    await replace_with_text(callback.message, tr(None, "cat_categories"), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("acat:"))
async def admin_catalog_callback(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin(callback):
        return
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]
    try:
        if action == "categories":
            await state.clear()
            await category_list(callback, int(parts[2]))
        elif action == "category":
            await state.clear()
            row = next((row for row in await catalog.repository.categories() if row["id"] == int(parts[2])), None)
            if row is None:
                raise ValueError("category")
            builder = InlineKeyboardBuilder()
            builder.button(text=tr(None, "cat_edit_category"), callback_data=f"acat:editcategory:{row['id']}")
            builder.button(text=tr(None, "admin_delete_button"), callback_data=f"acat:deletecategory:{row['id']}")
            builder.button(text=tr(None, "back"), callback_data="acat:categories:0")
            builder.adjust(1)
            await replace_with_text(callback.message, f"#{row['id']} · {html.escape(row['name'])} · {row['position']}", reply_markup=builder.as_markup())
        elif action in {"addcategory", "editcategory", "deletecategory", "trend", "field"}:
            await state.clear()
            data = {"kind": action}
            back = "acat:categories:0" if "category" in action else "admin_panel"
            if action in {"editcategory", "deletecategory"}:
                data["category_id"] = int(parts[2])
                if not any(row["id"] == data["category_id"] for row in await catalog.repository.categories()):
                    raise ValueError("category")
            if action == "field":
                series_id, field = int(parts[2]), parts[3]
                if field not in FIELDS or not catalog.series.get_series_by_id(series_id):
                    raise ValueError("field")
                data.update(series_id=series_id, field=field)
                back = f"admin_select_edit:{series_id}"
                text = tr(None, "cat_prompt_" + field)
                item = catalog.series.get_series_by_id(series_id)
                text += "\n\n" + html.escape(json.dumps(item[field], ensure_ascii=False)[:1000])
                if field == "category_ids":
                    text += "\n\n" + "\n".join(f"{row['id']}: {html.escape(row['name'])}" for row in await catalog.repository.categories())[:2500]
            elif action == "trend":
                text = tr(None, "cat_prompt_trend") + f"\n{await catalog.repository.trend_days()}"
            elif action == "deletecategory":
                text = tr(None, "cat_delete_category_confirm")
            else:
                text = tr(None, "cat_prompt_category")
            data["back"] = back
            response = await replace_with_text(callback.message, text, reply_markup=controls(back, action == "deletecategory"))
            await state.set_data({**data, "prompt_id": response.message_id})
            await state.set_state(CatalogAdminStates.confirm if action == "deletecategory" else CatalogAdminStates.input)
        elif action == "save":
            data = await state.get_data()
            if await state.get_state() != CatalogAdminStates.confirm.state or callback.message.message_id != data.get("prompt_id"):
                raise ValueError("stale")
            kind = data["kind"]
            if kind == "field":
                await catalog.edit_metadata(data["series_id"], **{data["field"]: data["value"]})
            elif kind in {"addcategory", "editcategory"}:
                await catalog.repository.save_category(data["name"], data["position"], data.get("category_id"))
            elif kind == "deletecategory":
                await catalog.repository.delete_category(data["category_id"])
            elif kind == "trend":
                await catalog.repository.set_trend_days(data["value"])
            else:
                raise ValueError("kind")
            await state.clear()
            await replace_with_text(callback.message, tr(None, "cat_saved"), reply_markup=controls(data["back"]))
        else:
            raise ValueError("action")
    except (ValueError, IndexError, KeyError, CatalogError):
        await state.clear()
        await replace_with_text(callback.message, tr(None, "cat_invalid"), reply_markup=controls())


@router.message(CatalogAdminStates.input)
async def admin_catalog_input(message: Message, state: FSMContext):
    if not await ensure_admin(message):
        await state.clear()
        return
    data = await state.get_data()
    try:
        text = message.text or ""
        if len(text) > 16000:
            raise ValueError("input too long")
        if data["kind"] == "field":
            value = parse_field(data["field"], text)
            validate_metadata({data["field"]: value})
            if data["field"] == "category_ids":
                existing = {row["id"] for row in await catalog.repository.categories()}
                if set(value) - existing:
                    raise ValueError("category")
            data["value"] = value
        elif data["kind"] == "trend":
            data["value"] = int(text)
            if not 1 <= data["value"] <= 365:
                raise ValueError("trend")
        else:
            name, position = text.rsplit("|", 1)
            data.update(name=" ".join(name.split()), position=int(position.strip()))
            if not 1 <= len(data["name"]) <= 60 or not -100000 <= data["position"] <= 100000:
                raise ValueError("category")
    except (ValueError, KeyError, TypeError):
        await message.answer(tr(None, "cat_invalid"), reply_markup=controls(data.get("back", "admin_panel")))
        return
    preview = html.escape(text[:1500])
    response = await message.answer(tr(None, "cat_confirm_changes") + "\n\n" + preview, reply_markup=controls(data["back"], True))
    await state.set_data({**data, "prompt_id": response.message_id})
    await state.set_state(CatalogAdminStates.confirm)
