from __future__ import annotations

import html

from aiogram import F,Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import CallbackQuery,Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.economy_repository import EconomyRepository,EconomyError
from handlers.admin.panel import ensure_admin
from handlers.profile import history_text
from services.i18n import tr
from utils.telegram import replace_with_text

router=Router()
economy=EconomyRepository()


class EconomyStates(StatesGroup):
    input=State()
    confirm=State()


def controls(confirm=False):
    builder=InlineKeyboardBuilder()
    if confirm:
        builder.button(text=tr(None,"admin_confirm"),callback_data="ec:commit")
    builder.button(text=tr(None,"back"),callback_data="ec:home")
    builder.button(text=tr(None,"admin_cancel"),callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


async def user_screen(message,user_id):
    user=await economy.profile(user_id)
    builder=InlineKeyboardBuilder()
    for mode in ("credit","debit"):
        builder.button(text=tr(None,"pt_admin_"+mode),callback_data=f"ec:adjust:{user_id}:{mode}")
    builder.button(text=tr(None,"pt_history"),callback_data=f"ec:history:{user_id}:0")
    builder.button(text=tr(None,"back"),callback_data="ec:home")
    builder.adjust(1)
    text=f"ID {user_id} · {html.escape((user['first_name'] or user['username'] or '—')[:100])}\n"+tr(None,"pt_balances",balance=user["points_balance"],earned=user["points_earned"],spent=user["points_spent"])
    await message.answer(text,reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("ec:"))
async def economy_callback(callback:CallbackQuery,state:FSMContext):
    if not await ensure_admin(callback):
        return
    await callback.answer()
    parts=callback.data.split(":")
    action=parts[1]
    try:
        if action!="commit":
            await state.clear()
        if action=="home":
            await state.clear()
            settings=await economy.settings()
            stats=await economy.statistics()
            builder=InlineKeyboardBuilder()
            for item in ("lookup","reward","metric","leaders","suspicious","legacy"):
                builder.button(text=tr(None,"pt_admin_"+item),callback_data=f"ec:{item}")
            builder.button(text=tr(None,"back"),callback_data="admin_panel")
            builder.adjust(1)
            text=tr(None,"pt_admin_title")+"\n"+tr(None,"pt_admin_stats",**stats)+"\n"+tr(None,"pt_reward",amount=settings["view_reward"])+"\n"+tr(None,"pt_metric_"+settings["leaderboard"])
            await replace_with_text(callback.message,text,reply_markup=builder.as_markup())
        elif action in {"lookup","reward","adjust"}:
            await state.clear()
            data={"kind":action}
            if action=="adjust":
                if parts[3] not in {"credit","debit"}:
                    raise EconomyError("invalid")
                await economy.profile(int(parts[2]))
                data.update(user_id=int(parts[2]),mode=parts[3],stage="amount")
                text=tr(None,"pt_prompt_amount")
            else:
                text=tr(None,"pt_prompt_"+action)
            await state.set_data(data)
            await state.set_state(EconomyStates.input)
            await replace_with_text(callback.message,text,reply_markup=controls())
        elif action=="user":
            await state.clear()
            await user_screen(callback.message,int(parts[2]))
        elif action=="history":
            await state.clear()
            user_id=int(parts[2])
            await economy.profile(user_id)
            rows,page,pages=await economy.history(user_id,int(parts[3]))
            builder=InlineKeyboardBuilder()
            if page:
                builder.button(text="‹",callback_data=f"ec:history:{user_id}:{page-1}")
            if page+1<pages:
                builder.button(text="›",callback_data=f"ec:history:{user_id}:{page+1}")
            builder.button(text=tr(None,"back"),callback_data=f"ec:user:{user_id}")
            builder.adjust(1)
            text=f"ID {user_id}\n"+history_text(rows,None)+"\n"+"\n".join(f"#{row['id']} · {tr(None,'rv_actor')}: {row['actor_id'] or '—'}" for row in rows)
            await replace_with_text(callback.message,text,reply_markup=builder.as_markup())
        elif action=="metric":
            await state.clear()
            if len(parts)==2:
                builder=InlineKeyboardBuilder()
                for value in ("earned","balance"):
                    builder.button(text=tr(None,"pt_metric_"+value),callback_data=f"ec:metric:{value}")
                builder.button(text=tr(None,"back"),callback_data="ec:home")
                builder.adjust(1)
                await replace_with_text(callback.message,tr(None,"pt_admin_metric"),reply_markup=builder.as_markup())
            else:
                if parts[2] not in {"earned","balance"}:
                    raise EconomyError("invalid")
                response=await replace_with_text(callback.message,tr(None,"pt_metric_"+parts[2]),reply_markup=controls(True))
                await state.set_data({"kind":"metric","value":parts[2],"prompt_id":response.message_id})
                await state.set_state(EconomyStates.confirm)
        elif action=="leaders":
            from handlers.profile import show_leaderboard
            await show_leaderboard(callback.message,callback.from_user.id,None,int(parts[2]) if len(parts)>2 else 0,"ec:home",economy,"ec:leaders")
        elif action=="suspicious":
            page=max(0,int(parts[2]) if len(parts)>2 else 0)
            rows,more=await economy.suspicious(page)
            text=tr(None,"pt_suspicious_notice")+"\n\n"
            for row in rows:
                text+=f"ID {row['telegram_id']} · {row['points_balance']} / {row['ledger_balance']} · {row['daily']}\n"
            if not rows:
                text+=tr(None,"pt_empty")
            builder=InlineKeyboardBuilder()
            if page:
                builder.button(text="‹",callback_data=f"ec:suspicious:{page-1}")
            if more:
                builder.button(text="›",callback_data=f"ec:suspicious:{page+1}")
            builder.button(text=tr(None,"back"),callback_data="ec:home")
            builder.adjust(1)
            await replace_with_text(callback.message,text,reply_markup=builder.as_markup())
        elif action=="legacy":
            await state.clear()
            preview=await economy.legacy_preview(callback.from_user.id)
            response=await replace_with_text(callback.message,tr(None,"pt_legacy_preview",**preview),reply_markup=controls(True))
            await state.set_data({"kind":"legacy","preview":preview,"prompt_id":response.message_id})
            await state.set_state(EconomyStates.confirm)
        elif action=="commit":
            data=await state.get_data()
            if await state.get_state()!=EconomyStates.confirm.state or data.get("prompt_id")!=callback.message.message_id:
                raise EconomyError("stale")
            if data["kind"]=="adjust":
                await economy.adjust(callback.from_user.id,data["user_id"],data["amount"],data["reason"],f"admin:{callback.from_user.id}:{callback.message.chat.id}:{callback.message.message_id}")
            elif data["kind"] in {"reward","metric"}:
                await economy.configure(callback.from_user.id,"view_reward" if data["kind"]=="reward" else "leaderboard",data["value"])
            elif data["kind"]=="legacy":
                await economy.legacy_apply(callback.from_user.id,data["preview"])
            else:
                raise EconomyError("invalid")
            await state.clear()
            await replace_with_text(callback.message,tr(None,"pt_saved"),reply_markup=controls())
        else:
            raise EconomyError("invalid")
    except (ValueError,KeyError,IndexError) as exc:
        await state.clear()
        code=str(exc) if isinstance(exc,EconomyError) else "invalid"
        await replace_with_text(callback.message,tr(None,"pt_error_"+code),reply_markup=controls())


@router.message(EconomyStates.input)
async def economy_input(message:Message,state:FSMContext):
    if not await ensure_admin(message):
        await state.clear()
        return
    data=await state.get_data()
    text=(message.text or "").strip()
    try:
        if data["kind"]=="lookup":
            await user_screen(message,int(text))
            await state.clear()
            return
        if data["kind"]=="adjust":
            if data["stage"]=="amount":
                amount=int(text)
                if not 1<=amount<=1000000000:
                    raise EconomyError("invalid")
                data.update(amount=amount if data["mode"]=="credit" else -amount,stage="reason")
                await state.set_data(data)
                await message.answer(tr(None,"pt_prompt_reason"),reply_markup=controls())
                return
            if not 1<=len(text)<=500:
                raise EconomyError("invalid")
            data["reason"]=text
            preview=f"ID {data['user_id']} · {data['amount']:+d}\n{html.escape(text)}"
        elif data["kind"]=="reward":
            value=int(text)
            if not 0<=value<=1000000:
                raise EconomyError("invalid")
            data["value"]=value
            preview=tr(None,"pt_reward",amount=value)
        else:
            raise EconomyError("invalid")
        response=await message.answer(tr(None,"cat_confirm_changes")+"\n\n"+preview,reply_markup=controls(True))
        await state.set_data({**data,"prompt_id":response.message_id})
        await state.set_state(EconomyStates.confirm)
    except (ValueError,KeyError) as exc:
        code=str(exc) if isinstance(exc,EconomyError) else "invalid"
        await message.answer(tr(None,"pt_error_"+code),reply_markup=controls())
