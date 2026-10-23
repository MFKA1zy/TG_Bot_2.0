from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class CaptchaService:
    def __init__(self, asset_dir: str | Path | None = None, answers_path: str | Path | None = None) -> None:
        self.asset_dir = Path(asset_dir) if asset_dir else Path(__file__).resolve().parent.parent / "assets" / "captcha"
        self.answers_path = Path(answers_path) if answers_path else Path(__file__).resolve().parent.parent / "data" / "captcha_answers.json"

    def is_available(self) -> bool:
        if not self.asset_dir.exists() or not self.answers_path.exists():
            return False
        answers = self._load_answers()
        return any(
            (self.asset_dir / f"{image_id}.png").is_file()
            and re.fullmatch(r"\d{5}", answers.get(image_id, ""))
            for image_id in map(str, range(1, 6))
        )

    def _load_answers(self) -> dict[str, str]:
        if not self.answers_path.exists():
            return {}
        try:
            raw = self.answers_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.exception("Unable to load CAPTCHA answers.")
            return {}
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, value in payload.items():
            if isinstance(value, str) and re.fullmatch(r"\d{5}", value):
                normalized[str(key)] = value
        return normalized

    def get_answer_for(self, image_file: str) -> str:
        answers = self._load_answers()
        return answers.get(str(Path(image_file).stem), "")

    def get_challenge(self) -> dict[str, str] | None:
        if not self.is_available():
            return None

        answers = self._load_answers()
        candidates = [
            self.asset_dir / f"{image_id}.png"
            for image_id in map(str, range(1, 6))
            if (self.asset_dir / f"{image_id}.png").is_file()
            and image_id in answers
        ]
        if not candidates:
            return None

        chosen = random.choice(candidates)
        return {"image_file": chosen.name, "answer": answers[chosen.stem]}

    def validate(self, image_file: str, user_input: str) -> bool:
        expected = self.get_answer_for(image_file)
        if not expected:
            return False
        return bool(re.fullmatch(r"\d{5}", user_input or "")) and user_input == expected
