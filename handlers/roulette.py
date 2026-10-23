from __future__ import annotations

import asyncio
import logging

from aiogram import F,Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.economy_repository import EconomyError
from database.roulette_repository import RouletteRepository,RouletteError
from services.access_service import authorize
from services.i18n import tr
from utils.telegram import replace_with_text

router=Router()
roulette=RouletteRepository()
logger=logging.getLogger(__name__)


def time_text(language,remaining):
    remaining=max(0,int(remaining))
    return tr(language,"rl_time",hours=remaining//3600,minutes=remaining%3600//60,seconds=remaining%60)


def buttons(language,spin=False,back="main_menu"):
    builder=InlineKeyboardBuilder()
    if spin:
        builder.button(text=tr(language,"rl_spin"),callback_data="rr:spin")
    builder.button(text=tr(language,"rl_history"),callback_data="rr:history:0")
    builder.button(text=tr(language,"back"),callback_data=back)
    builder.adjust(1)
    return builder.as_markup()


async def show_history(message,user_id,language,page=0,repository=None,prefix="rr:history",back="rr:home"):
    rows,page,pages=await (repository or roulette).history(user_id,page)
    text=tr(language,"rl_history")+f" · {page+1}/{pages}\n\n"
    for row in rows:
        text+=f"#{row['id']} · {row['spun_at'][:19].replace('T',' ')} UTC — +{row['awarded_points']}\n"
    if not rows:
        text+=tr(language,"pt_empty")
    builder=InlineKeyboardBuilder()
    if page:
        builder.button(text="‹",callback_data=f"{prefix}:{page-1}")
    if page+1<pages:
        builder.button(text="›",callback_data=f"{prefix}:{page+1}")
    builder.button(text=tr(language,"back"),callback_data=back)
    builder.adjust(1)
    await replace_with_text(message,text,reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("rr:"))
async def roulette_callback(callback:CallbackQuery,state:FSMContext):
    language=await authorize(callback)
    if language is None:
        return
    await state.clear()
    parts=callback.data.split(":")
    try:
        action=parts[1]
        if action=="home" and len(parts)==2:
            status=await roulette.status(callback.from_user.id)
            available=int(bool(status["enabled"]) and status["remaining"]==0)
            text=tr(language,"rl_home",balance=status["balance"],available=available)
            if not status["enabled"]:
                text+="\n"+tr(language,"rl_error_disabled")
            elif status["remaining"]:
                text+="\n"+tr(language,"rl_next",time=time_text(language,status["remaining"]))
            await replace_with_text(callback.message,text,reply_markup=buttons(language,available))
        elif action=="history" and len(parts)==3:
            await show_history(callback.message,callback.from_user.id,language,int(parts[2]))
        elif action=="spin" and len(parts)==2:
            # The transaction has finished before any animation or Telegram call.
            result=await roulette.spin(callback.from_user.id,f"callback:{callback.id}")
            key="rl_replayed" if result["replayed"] else "rl_win" if result["awarded_points"] else "rl_zero"
            text=tr(language,key,amount=result["awarded_points"],balance=result["balance"])+"\n"+tr(language,"rl_next",time=time_text(language,result["remaining"]))
            if not result["replayed"]:
                try:
                    await replace_with_text(callback.message,tr(language,"rl_animation"),reply_markup=None)
                except TelegramAPIError as exc:
                    logger.info("Roulette animation unavailable (%s).",type(exc).__name__)
                else:
                    await asyncio.sleep(.15)
            try:
                await replace_with_text(callback.message,text,reply_markup=buttons(language,back="rr:home"))
            except TelegramAPIError:
                await callback.message.answer(text,reply_markup=buttons(language,back="rr:home"))
        else:
            raise RouletteError("invalid")
    except (EconomyError,ValueError,IndexError) as exc:
        code=str(exc) if isinstance(exc,EconomyError) else "invalid"
        if code=="cooldown":
            text=tr(language,"rl_cooldown",time=time_text(language,exc.remaining))
        else:
            text=tr(language,"rl_error_"+code)
        await replace_with_text(callback.message,text,reply_markup=buttons(language,back="rr:home"))
