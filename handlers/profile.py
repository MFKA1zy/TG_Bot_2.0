from __future__ import annotations

import asyncio
import html

from aiogram import F,Router
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.economy_repository import EconomyRepository,EconomyError
from services.access_service import authorize
from services.catalog_service import CatalogService
from services.i18n import tr
from utils.telegram import replace_with_text

router=Router()
economy=EconomyRepository()
catalog=CatalogService()


async def show_leaderboard(message,user_id,language,page=0,back="pf:home",repository=None,prefix="pf:leaders"):
    rows,page,pages,own,metric=await (repository or economy).leaderboard(user_id,page)
    text=f"<b>{tr(language,'pt_leaders')}</b> · {tr(language,'pt_metric_'+metric)}\n\n"
    for offset,row in enumerate(rows,page*10+1):
        name=html.escape(" ".join((" ".join(filter(None,[row["first_name"],row["last_name"]])) or row["username"] or "—").split())[:60])
        text+=f"{offset}. {name} — {row['points']}\n"
    if own:
        text+="\n"+tr(language,"pt_own_rank",rank=own["rank"] or "—",points=own["points"])
    builder=InlineKeyboardBuilder()
    if page:
        builder.button(text="‹",callback_data=f"{prefix}:{page-1}")
    if page+1<pages:
        builder.button(text="›",callback_data=f"{prefix}:{page+1}")
    builder.button(text=tr(language,"back"),callback_data=back)
    builder.adjust(1)
    await replace_with_text(message,text,reply_markup=builder.as_markup())


def history_text(rows,language):
    text=tr(language,"pt_history")+"\n\n"
    for row in rows:
        reason=tr(language,"pt_reason_"+row["reason"]) if row["reason"] in {"view","legacy_view"} else row["reason"]
        text+=f"#{row['id']} · {row['created_at'][:10]} · {row['amount']:+d} · {tr(language,'pt_op_'+row['operation'])}\n{html.escape(reason[:100])}"
        if row["series_id"] is not None:
            text+=f" · #{row['series_id']} {html.escape((row['series_title'] or '')[:70])}"
        text+="\n"
    return text if rows else text+tr(language,"pt_empty")


@router.callback_query(F.data.startswith("pf:"))
async def profile_callback(callback:CallbackQuery,state:FSMContext):
    language=await authorize(callback)
    if language is None:
        return
    await state.clear()
    parts=callback.data.split(":")
    action=parts[1]
    try:
        builder=InlineKeyboardBuilder()
        if action=="home":
            user=await economy.profile(callback.from_user.id)
            text=tr(language,"pt_profile",name=html.escape((" ".join(filter(None,[user["first_name"],user["last_name"]])) or "—")[:100]),username=html.escape("@"+user["username"] if user["username"] else "—"),date=(user["created_at"] or "—")[:10],balance=user["points_balance"],earned=user["points_earned"],spent=user["points_spent"],watched=user["watched"],ratings=user["ratings"],reviews=user["reviews"],favorites=user["favorites"],rank=user["rank"] or "—")
            for section in ("watched","favorites","ratings","reviews"):
                builder.button(text=tr(language,"pt_"+section),callback_data=f"pf:items:{section}:0")
            builder.button(text=tr(language,"pt_history"),callback_data="pf:history:0")
            builder.button(text=tr(language,"pt_leaders"),callback_data="pf:leaders:0")
            builder.button(text=tr(language,"back"),callback_data="main_menu")
        elif action=="leaders":
            await show_leaderboard(callback.message,callback.from_user.id,language,int(parts[2]))
            return
        elif action=="history":
            rows,page,pages=await economy.history(callback.from_user.id,int(parts[2]))
            text=history_text(rows,language)+f"\n{page+1}/{pages}"
            if page:
                builder.button(text="‹",callback_data=f"pf:history:{page-1}")
            if page+1<pages:
                builder.button(text="›",callback_data=f"pf:history:{page+1}")
            builder.button(text=tr(language,"back"),callback_data="pf:home")
        elif action=="items":
            section=parts[2]
            rows,page,pages=await economy.personal_items(callback.from_user.id,section,int(parts[3]))
            def resolve():
                return [(row,catalog.published(row["series_id"])) for row in rows]
            text=tr(language,"pt_"+section)+f" · {page+1}/{pages}\n\n"
            for row,item in await asyncio.to_thread(resolve):
                name=item["title"][:60] if item else tr(language,"pt_archived")+f" #{row['series_id']}"
                label=(f" · {row['score']}/10" if section=="ratings" else " · "+tr(language,"rv_admin_"+row["status"]) if section=="reviews" else "")
                text+=html.escape(name)+label+"\n"
                if item:
                    target=f"rev:own:{item['id']}:all:0:0" if section=="reviews" else f"cat:card:{item['id']}:all:0:0"
                    builder.button(text=name,callback_data=target)
            if not rows:
                text+=tr(language,"pt_empty")
            if page:
                builder.button(text="‹",callback_data=f"pf:items:{section}:{page-1}")
            if page+1<pages:
                builder.button(text="›",callback_data=f"pf:items:{section}:{page+1}")
            builder.button(text=tr(language,"back"),callback_data="pf:home")
        else:
            raise EconomyError("invalid")
        builder.adjust(1)
        await replace_with_text(callback.message,text,reply_markup=builder.as_markup())
    except (ValueError,KeyError,IndexError) as exc:
        code=str(exc) if isinstance(exc,EconomyError) else "invalid"
        builder=InlineKeyboardBuilder()
        builder.button(text=tr(language,"back"),callback_data="pf:home")
        await replace_with_text(callback.message,tr(language,"pt_error_"+code),reply_markup=builder.as_markup())
