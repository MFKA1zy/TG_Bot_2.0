from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class UserRecord:
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_admin: bool = False
    captcha_passed: bool = False
    language: str | None = None
    created_at: str | None = None
    last_activity: str | None = None
    is_blocked: bool = False

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "UserRecord":
        return cls(
            telegram_id=row.get("telegram_id", 0),
            username=row.get("username"),
            first_name=row.get("first_name"),
            last_name=row.get("last_name"),
            is_admin=bool(row.get("is_admin", 0)),
            captcha_passed=bool(row.get("captcha_passed", 0)),
            language=row.get("language"),
            created_at=row.get("created_at"),
            last_activity=row.get("last_activity"),
            is_blocked=bool(row.get("is_blocked", 0)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "telegram_id": self.telegram_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_admin": int(self.is_admin),
            "captcha_passed": int(self.captcha_passed),
            "language": self.language,
            "created_at": self.created_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_activity": self.last_activity
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "is_blocked": int(self.is_blocked),
        }
