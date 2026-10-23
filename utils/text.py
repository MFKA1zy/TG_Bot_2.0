from __future__ import annotations

import re


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def normalize_query(value: str | None) -> str:
    return normalize_text(value).lower()


def slugify(value: str | None) -> str:
    text = normalize_text(value)
    return re.sub(r"[^a-zA-Z0-9\-\s]", "", text).strip().lower()
