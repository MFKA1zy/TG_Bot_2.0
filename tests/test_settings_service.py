from types import SimpleNamespace

import pytest

import services.settings_service as settings_service


def test_welcome_text_is_saved_atomically_and_reloaded(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(
        settings_service,
        "settings",
        SimpleNamespace(SETTINGS_PATH=path),
    )

    settings_service.save_welcome_text("Привет!\nДобро пожаловать 🌟")

    assert settings_service.get_welcome_text() == "Привет!\nДобро пожаловать 🌟"
    assert not list(tmp_path.glob(".*.tmp"))


def test_welcome_texts_are_saved_independently_by_language(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(
        settings_service,
        "settings",
        SimpleNamespace(SETTINGS_PATH=path),
    )

    settings_service.save_welcome_text("Українське привітання", "uk")
    settings_service.save_welcome_text("English greeting", "en")

    assert settings_service.get_welcome_text("uk") == "Українське привітання"
    assert settings_service.get_welcome_text("en") == "English greeting"
    assert "Добро пожаловать" in settings_service.get_welcome_text("ru")


def test_corrupt_settings_are_backed_up_not_replaced(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(
        settings_service,
        "settings",
        SimpleNamespace(SETTINGS_PATH=path),
    )

    with pytest.raises(settings_service.SettingsFileError):
        settings_service.get_welcome_text()

    backups = list(tmp_path.glob("settings.json.corrupt.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken"
    assert path.read_text(encoding="utf-8") == "{broken"
