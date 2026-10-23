from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_ACTIVATION_CODE: str = os.getenv("ADMIN_ACTIVATION_CODE", "")
    ADMIN_IDS: tuple[int, ...] = tuple(
        int(item.strip())
        for item in os.getenv("ADMIN_IDS", "").split(",")
        if item.strip()
    )
    ADMIN_CODE_ACTIVATION_ENABLED: bool = (
        os.getenv("ADMIN_CODE_ACTIVATION_ENABLED", "true").strip().lower() == "true"
    )
    TELEGRAM_PROXY: str = os.getenv("TELEGRAM_PROXY", "").strip()
    TELEGRAM_REQUEST_TIMEOUT: float = float(
        os.getenv("TELEGRAM_REQUEST_TIMEOUT", "20")
    )
    IMGBB_API_KEY: str = os.getenv("IMGBB_API_KEY", "").strip()
    BASE_DIR: Path = BASE_DIR
    DB_PATH: Path = BASE_DIR / "database" / "users.db"
    SERIES_PATH: Path = BASE_DIR / "data" / "series.json"
    SETTINGS_PATH: Path = BASE_DIR / "data" / "settings.json"
    CAPTCHA_ANSWERS_PATH: Path = BASE_DIR / "data" / "captcha_answers.json"
    CAPTCHA_ASSET_DIR: Path = BASE_DIR / "assets" / "captcha"
    LOGS_DIR: Path = BASE_DIR / "logs"


settings = Settings()
