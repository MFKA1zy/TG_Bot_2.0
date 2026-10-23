from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timezone

from config import settings
from services.i18n import normalize_language, tr

_SETTINGS_LOCK = threading.RLock()
DEFAULT_WELCOME = tr("ru", "welcome")


class SettingsFileError(RuntimeError):
    pass


def _backup_corrupt_settings() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = settings.SETTINGS_PATH.with_name(
        f"{settings.SETTINGS_PATH.name}.corrupt.{timestamp}.bak"
    )
    suffix = 1
    while backup.exists():
        backup = settings.SETTINGS_PATH.with_name(
            f"{settings.SETTINGS_PATH.name}.corrupt.{timestamp}.{suffix}.bak"
        )
        suffix += 1
    try:
        shutil.copy2(settings.SETTINGS_PATH, backup)
    except OSError as exc:
        raise SettingsFileError(
            "Настройки повреждены, но резервную копию создать не удалось."
        ) from exc
    return backup.name


def _read_settings() -> dict:
    if not settings.SETTINGS_PATH.exists():
        return {"welcome_text": DEFAULT_WELCOME}
    try:
        payload = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        backup_name = _backup_corrupt_settings()
        raise SettingsFileError(
            f"Файл настроек повреждён; создана копия {backup_name}."
        ) from exc
    except OSError as exc:
        raise SettingsFileError("Не удалось прочитать настройки бота.") from exc
    if not isinstance(payload, dict):
        backup_name = _backup_corrupt_settings()
        raise SettingsFileError(
            f"Структура настроек повреждена; создана копия {backup_name}."
        )
    return payload


def get_welcome_text(language: str | None = None) -> str:
    language = normalize_language(language)
    with _SETTINGS_LOCK:
        payload = _read_settings()
    welcome_texts = payload.get("welcome_texts", {})
    if not isinstance(welcome_texts, dict):
        raise SettingsFileError("В файле настроек некорректны приветствия.")
    text = welcome_texts.get(language, tr(language, "welcome"))
    if not isinstance(text, str) or not text.strip():
        raise SettingsFileError("В файле настроек некорректен текст приветствия.")
    return text.strip()


def save_welcome_text(text: str, language: str | None = None) -> None:
    language = normalize_language(language)
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Текст приветствия не может быть пустым.")
    with _SETTINGS_LOCK:
        payload = _read_settings()
        welcome_texts = payload.setdefault("welcome_texts", {})
        if not isinstance(welcome_texts, dict):
            raise SettingsFileError("В файле настроек некорректны приветствия.")
        welcome_texts[language] = clean_text
        settings.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = settings.SETTINGS_PATH.with_name(
            f".{settings.SETTINGS_PATH.name}.{os.getpid()}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, settings.SETTINGS_PATH)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise SettingsFileError("Не удалось сохранить приветствие.") from exc
