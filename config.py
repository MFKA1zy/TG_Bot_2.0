from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _as_path(value: str | None, default: Path) -> Path:
    raw = (value or "").strip()
    if not raw:
        return default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (BASE_DIR / candidate).resolve()


def _read_bool(*names: str, default: bool = False) -> bool:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _read_channel_id() -> str | int | None:
    for name in ("SUBSCRIPTION_CHANNEL_ID", "CHANNEL_ID", "TELEGRAM_CHANNEL_ID"):
        value = os.getenv(name)
        if value is None:
            continue
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.startswith("@"):
            return stripped
        try:
            return int(stripped)
        except ValueError:
            return stripped
    return None


def _default_db_path(data_dir: Path) -> Path:
    legacy = BASE_DIR / "database" / "users.db"
    if not os.getenv("DATA_DIR", "").strip() and legacy.is_file():
        return legacy
    return data_dir / "users.db"


@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_ACTIVATION_CODE: str = os.getenv("ADMIN_ACTIVATION_CODE", "")
    ADMIN_IDS: tuple[int, ...] = tuple(
        int(item.strip())
        for item in os.getenv("ADMIN_IDS", "").split(",")
        if item.strip()
    )
    ADMIN_CODE_ACTIVATION_ENABLED: bool = _read_bool(
        "ADMIN_CODE_ACTIVATION_ENABLED",
        default=True,
    )
    TELEGRAM_PROXY: str = os.getenv("TELEGRAM_PROXY", "").strip()
    TELEGRAM_REQUEST_TIMEOUT: float = float(
        os.getenv("TELEGRAM_REQUEST_TIMEOUT", "20")
    )
    IMGBB_API_KEY: str = os.getenv("IMGBB_API_KEY", "").strip()
    DATA_DIR: Path = _as_path(os.getenv("DATA_DIR"), BASE_DIR / "data")
    BASE_DIR: Path = BASE_DIR
    DB_PATH: Path = _as_path(os.getenv("DB_PATH"), _default_db_path(DATA_DIR))
    SERIES_PATH: Path = DATA_DIR / "series.json"
    SETTINGS_PATH: Path = DATA_DIR / "settings.json"
    CAPTCHA_ANSWERS_PATH: Path = DATA_DIR / "captcha_answers.json"
    CAPTCHA_ASSET_DIR: Path = BASE_DIR / "assets" / "captcha"
    LOGS_DIR: Path = _as_path(os.getenv("LOGS_DIR"), DATA_DIR / "logs")
    SUBSCRIPTION_REQUIRED: bool = _read_bool(
        "SUBSCRIPTION_REQUIRED",
        "REQUIRE_SUBSCRIPTION",
        "MANDATORY_SUBSCRIPTION",
        default=False,
    )
    SUBSCRIPTION_CHANNEL_ID: str | int | None = _read_channel_id()
    SUBSCRIPTION_CHANNEL_URL: str = (
        os.getenv("SUBSCRIPTION_CHANNEL_URL")
        or os.getenv("CHANNEL_URL")
        or os.getenv("TELEGRAM_CHANNEL_URL")
        or ""
    ).strip()


settings = Settings()
