from services.i18n import tr


def add_navigation(builder, language=None, back=None):
    if back and back != "main_menu":
        builder.button(text=tr(language, "back"), callback_data=back)
    builder.button(text=tr(language, "main_menu"), callback_data="main_menu")
