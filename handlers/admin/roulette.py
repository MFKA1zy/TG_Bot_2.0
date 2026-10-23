from __future__ import annotations

import html

from aiogram import F,Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import CallbackQuery,Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.economy_repository import EconomyError
from database.roulette_repository import RouletteRepository,RouletteError,validate_prizes
from handlers.admin.panel import ensure_admin
from handlers.roulette import show_history
from services.i18n import tr
from utils.telegram import replace_with_text

router=Router()
roulette=RouletteRepository()


class RouletteAdminStates(StatesGroup):
    input=State()
    confirm=State()


def controls(confirm=False):
    builder=InlineKeyboardBuilder()
    if confirm:
        builder.button(text=tr(None,"admin_confirm"),callback_data="ra:commit")
    builder.button(text=tr(None,"back"),callback_data="ra:home")
    builder.button(text=tr(None,"admin_cancel"),callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def prize_lines(prizes):
    return "\n".join(f"{row['id']} | {row['points']} | {row['weight']} | {int(row['enabled'])}" for row in prizes)


def parse_prizes(text):
    prizes=[]
    for line in text.splitlines():
        columns=[value.strip() for value in line.split("|")]
        if len(columns)!=4 or columns[3] not in {"0","1"}:
            raise RouletteError("config")
        prize_id,points,weight,enabled=columns
        prizes.append({"id":None if prize_id=="+" else int(prize_id),"points":int(points),"weight":int(weight),"enabled":enabled=="1"})
    validate_prizes(prizes)
    return prizes


@router.callback_query(F.data.startswith("ra:"))
async def admin_roulette_callback(callback:CallbackQuery,state:FSMContext):
    if not await ensure_admin(callback):
        return
    await callback.answer()
    parts=callback.data.split(":")
    try:
        action=parts[1]
        if action!="commit":
            await state.clear()
        if action=="home":
            config=await roulette.config()
            stats=await roulette.statistics()
            text=tr(None,"rl_admin_title")+"\n"+tr(None,"rl_admin_summary",spins=stats["spins"],points=stats["points"],hours=config["cooldown_seconds"]//3600)+"\n"+tr(None,"rv_on" if config["enabled"] else "rv_off")+"\n\n"+tr(None,"rl_prize_header")+"\n"+prize_lines(config["prizes"])
            builder=InlineKeyboardBuilder()
            for name in ("prizes","toggle","interval","stats","user"):
                builder.button(text=tr(None,"rl_admin_"+name),callback_data=f"ra:{name}")
            builder.button(text=tr(None,"back"),callback_data="admin_panel")
            builder.adjust(1)
            await replace_with_text(callback.message,text,reply_markup=builder.as_markup())
        elif action in {"prizes","interval","user","toggle"}:
            config=await roulette.config()
            data={"kind":action,"version":config["version"]}
            if action=="toggle":
                data["enabled"]=not bool(config["enabled"])
                text=tr(None,"rl_toggle_confirm")+"\n"+tr(None,"rv_on" if data["enabled"] else "rv_off")
            else:
                text=tr(None,"rl_prompt_"+action)
                if action=="prizes":
                    text+="\n\n"+prize_lines(config["prizes"])
            response=await replace_with_text(callback.message,text,reply_markup=controls(action=="toggle"))
            data["prompt_id"]=response.message_id
            await state.set_data(data)
            await state.set_state(RouletteAdminStates.confirm if action=="toggle" else RouletteAdminStates.input)
        elif action=="commit":
            data=await state.get_data()
            if await state.get_state()!=RouletteAdminStates.confirm.state or data.get("prompt_id")!=callback.message.message_id:
                raise RouletteError("stale")
            kwargs={"prizes":data["prizes"]} if data["kind"]=="prizes" else {"cooldown_seconds":data["seconds"]} if data["kind"]=="interval" else {"enabled":data["enabled"]}
            await roulette.save_config(callback.from_user.id,data["version"],**kwargs)
            await state.clear()
            await replace_with_text(callback.message,tr(None,"cat_saved"),reply_markup=controls())
        elif action=="stats":
            stats=await roulette.statistics(int(parts[2]) if len(parts)==3 else 0)
            text=tr(None,"rl_stats",spins=stats["spins"],points=stats["points"])+"\n\n"+"\n".join(tr(None,"rl_distribution",amount=row["awarded_points"],spins=row["spins"]) for row in stats["distribution"])
            builder=InlineKeyboardBuilder()
            if stats['page']:
                builder.button(text='‹',callback_data=f"ra:stats:{stats['page']-1}")
            if stats['page']+1<stats['pages']:
                builder.button(text='›',callback_data=f"ra:stats:{stats['page']+1}")
            builder.button(text=tr(None,'back'),callback_data='ra:home')
            builder.adjust(1)
            await replace_with_text(callback.message,text,reply_markup=builder.as_markup())
        elif action=="history" and len(parts)==4:
            user_id=int(parts[2])
            await roulette.profile(user_id)
            await show_history(callback.message,user_id,None,int(parts[3]),roulette,f"ra:history:{user_id}","ra:home")
        else:
            raise RouletteError("invalid")
    except (EconomyError,ValueError,KeyError,IndexError) as exc:
        await state.clear()
        code=str(exc) if isinstance(exc,EconomyError) else "invalid"
        await replace_with_text(callback.message,tr(None,"rl_error_"+code),reply_markup=controls())


@router.message(RouletteAdminStates.input)
async def admin_roulette_input(message:Message,state:FSMContext):
    if not await ensure_admin(message):
        await state.clear()
        return
    data=await state.get_data()
    text=(message.text or "").strip()
    try:
        if data["kind"]=="user":
            user_id=int(text)
            await roulette.profile(user_id)
            builder=InlineKeyboardBuilder()
            builder.button(text=tr(None,"rl_history"),callback_data=f"ra:history:{user_id}:0")
            builder.button(text=tr(None,"back"),callback_data="ra:home")
            builder.adjust(1)
            await message.answer(f"ID {user_id}",reply_markup=builder.as_markup())
            await state.clear()
            return
        if data["kind"]=="prizes":
            if len(text)>3000:
                raise RouletteError("config")
            data["prizes"]=parse_prizes(text)
        elif data["kind"]=="interval":
            hours=int(text)
            if not 1<=hours<=720:
                raise RouletteError("config")
            data["seconds"]=hours*3600
        else:
            raise RouletteError("invalid")
        response=await message.answer(tr(None,"cat_confirm_changes")+"\n\n"+html.escape(text),reply_markup=controls(True))
        await state.set_data({**data,"prompt_id":response.message_id})
        await state.set_state(RouletteAdminStates.confirm)
    except (EconomyError,ValueError,KeyError) as exc:
        code=str(exc) if isinstance(exc,EconomyError) else "invalid"
        await message.answer(tr(None,"rl_error_"+code),reply_markup=controls())
