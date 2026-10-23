from keyboards.inline.admin_menu import (
    build_admin_menu,
    build_add_series_photo_prompt,
    build_edit_series_fields,
    build_edit_series_photo_prompt,
)
from keyboards.inline.user_menu import (
    build_main_menu,
)


def test_admin_menu_buttons_are_one_per_row():
    keyboard = build_admin_menu()

    assert len(keyboard.inline_keyboard) == 8
    assert all(len(row) == 1 for row in keyboard.inline_keyboard)
    assert [row[0].callback_data for row in keyboard.inline_keyboard] == [
        "admin_add_series",
        "admin_list_series",
        "admin_edit_series",
        "admin_delete_series",
        "admin_edit_welcome",
        "admin_broadcast",
        "admin_stats",
        "main_menu",
    ]


def test_main_menu_exposes_inline_search():
    keyboard = build_main_menu()

    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "🔎 Поиск"
    assert keyboard.inline_keyboard[0][0].switch_inline_query_current_chat == ""
    assert keyboard.inline_keyboard[1][0].callback_data == "user_settings"


def test_admin_photo_controls_have_skip_edit_remove_and_cancel_actions():
    add_photo = build_add_series_photo_prompt()
    edit_fields = build_edit_series_fields(17)
    edit_photo = build_edit_series_photo_prompt(17, has_photo=True)

    assert [row[0].callback_data for row in add_photo.inline_keyboard] == [
        "admin_skip_series_photo",
        "admin_cancel",
    ]
    assert [row[0].callback_data for row in edit_fields.inline_keyboard] == [
        "admin_edit_title:17",
        "admin_edit_url:17",
        "admin_edit_photo:17",
        "admin_cancel",
    ]
    assert [row[0].callback_data for row in edit_photo.inline_keyboard] == [
        "admin_remove_series_photo:17",
        "admin_cancel",
    ]
