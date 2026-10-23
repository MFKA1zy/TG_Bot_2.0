from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    waiting_activation_code = State()
    waiting_title = State()
    waiting_url = State()
    waiting_series_photo = State()
    waiting_confirmation = State()
    waiting_edit_title = State()
    waiting_edit_url = State()
    waiting_edit_confirmation = State()
    waiting_edit_series_photo = State()
    waiting_edit_photo_confirmation = State()
    waiting_photo_remove_confirmation = State()
    waiting_delete_confirmation = State()
    waiting_welcome_text = State()
    waiting_welcome_confirmation = State()
    waiting_broadcast_text = State()
    waiting_broadcast_confirm = State()
