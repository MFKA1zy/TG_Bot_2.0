import json
from pathlib import Path

from services.i18n import normalize_language, tr


LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"


def test_locales_have_matching_keys_and_valid_format_strings():
    locales = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in LOCALE_DIR.glob("*.json")
    }

    assert set(locales) == {"ru", "uk", "en"}
    assert set(locales["ru"]) == set(locales["uk"]) == set(locales["en"])
    format_values = {
        "name":"Name","username":"@user","date":"2026-10-24","balance":100,"earned":120,"spent":20,
        "watched":3,"favorites":2,"rank":1,"points":100,"operations":5,"credited":120,"debited":20,
        "users":2,"views":4,"amount":10,
        "average": "8.5", "votes": 12, "own": 9,
        "ratings": 20, "reviews": 10, "pending": 2, "reports": 1, "warnings": 3,
        "language": "English",
        "language_name": "English",
        "url": "https://example.invalid",
        "title": "Series",
        "photo_status": "Photo status",
        "status": "completed",
        "text": "Sample",
        "attempts": 2,
        "total": 10,
        "captcha": 8,
        "admins": 1,
        "series": 5,
        "today": 3,
        "week": 4,
        "blocked": 0,
        "broadcasts": 0,
        "recipients": 10,
        "processed": 10,
        "target": 10,
        "success": 10,
        "failed": 0,
    }
    for locale in locales.values():
        for text in locale.values():
            text.format(**format_values)


def test_language_normalization_falls_back_to_russian():
    assert normalize_language("uk-UA") == "uk"
    assert normalize_language("EN") == "en"
    assert normalize_language("fr-FR") == "ru"


def test_welcome_translations_use_the_requested_language():
    assert "Добро пожаловать" in tr("ru", "welcome")
    assert "Ласкаво просимо" in tr("uk", "welcome")
    assert "Welcome to Series Search TV" in tr("en", "welcome")
