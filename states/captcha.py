from aiogram.fsm.state import State, StatesGroup


class CaptchaStates(StatesGroup):
    waiting_for_answer = State()
