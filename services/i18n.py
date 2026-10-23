from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from aiogram import BaseMiddleware

SUPPORTED_LANGUAGES = ("ru", "uk", "en")
_LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"
_CURRENT_LANGUAGE: ContextVar[str] = ContextVar("current_language", default="ru")


def normalize_language(language: str | None) -> str:
    if language is None:
        return _CURRENT_LANGUAGE.get()
    code = (language or "").replace("_", "-").split("-", 1)[0].lower()
    return code if code in SUPPORTED_LANGUAGES else "ru"


def detect_language(language_code: str | None) -> str:
    return normalize_language(language_code)


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def _load_locale(language: str) -> dict[str, str]:
    path = _LOCALE_DIR / f"{language}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load locale file: {path}") from exc
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise RuntimeError(f"Locale file has an invalid structure: {path}")
    return payload


def tr(language: str | None, key: str, **values: object) -> str:
    normalized = normalize_language(language)
    locale = _load_locale(normalized)
    if key not in locale:
        raise KeyError(f"Missing translation key {key!r} in locale {normalized!r}")
    return locale[key].format(**values)


class LocaleMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)
        from database.repository import UserRepository

        user = await UserRepository().get_user_by_telegram_id(from_user.id)
        selected = (
            user.language
            if user and user.language
            else detect_language(getattr(from_user, "language_code", None))
        )
        token = _CURRENT_LANGUAGE.set(normalize_language(selected))
        try:
            return await handler(event, data)
        finally:
            _CURRENT_LANGUAGE.reset(token)
