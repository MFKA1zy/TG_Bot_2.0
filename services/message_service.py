from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from config import settings

logger = logging.getLogger(__name__)


class MessageService:
    def __init__(self, tracking_path: str | Path | None = None) -> None:
        self._tracking_path = Path(
            tracking_path
            if tracking_path is not None
            else settings.BASE_DIR / "data" / ".message_cleanup.json"
        )
        self._lock = threading.RLock()
        self._tracked_messages: dict[int, set[int]] = {}
        self._tracked_user_messages: dict[int, set[int]] = {}
        self._load_tracking_state()

    def track_message(self, chat_id: int, message_id: int) -> None:
        with self._lock:
            self._tracked_messages.setdefault(chat_id, set()).add(message_id)
            self._persist_tracking_state()

    def track_messages(self, chat_id: int, message_ids: list[int]) -> None:
        for message_id in message_ids:
            self.track_message(chat_id, message_id)

    def track_user_message(self, chat_id: int, message_id: int) -> None:
        with self._lock:
            self._tracked_user_messages.setdefault(chat_id, set()).add(message_id)
            self._persist_tracking_state()

    def _load_tracking_state(self) -> None:
        try:
            payload = json.loads(self._tracking_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            logger.exception(
                "Could not load saved message-cleanup state from %s.",
                self._tracking_path,
            )
            return
        if not isinstance(payload, dict):
            logger.error("Saved message-cleanup state has an invalid structure.")
            return
        self._tracked_messages = self._parse_tracking_map(payload.get("bot", {}))
        self._tracked_user_messages = self._parse_tracking_map(
            payload.get("user", {})
        )

    @staticmethod
    def _parse_tracking_map(value: object) -> dict[int, set[int]]:
        if not isinstance(value, dict):
            return {}
        parsed: dict[int, set[int]] = {}
        for chat_id, message_ids in value.items():
            if not isinstance(message_ids, list):
                continue
            try:
                parsed_chat_id = int(chat_id)
                parsed_ids = {int(message_id) for message_id in message_ids}
            except (TypeError, ValueError):
                continue
            if parsed_ids:
                parsed[parsed_chat_id] = parsed_ids
        return parsed

    def _persist_tracking_state(self) -> None:
        payload = {
            "bot": {
                str(chat_id): sorted(message_ids)
                for chat_id, message_ids in self._tracked_messages.items()
                if message_ids
            },
            "user": {
                str(chat_id): sorted(message_ids)
                for chat_id, message_ids in self._tracked_user_messages.items()
                if message_ids
            },
        }
        temp_path = self._tracking_path.with_name(
            f".{self._tracking_path.name}.{os.getpid()}.tmp"
        )
        try:
            self._tracking_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temp_path, self._tracking_path)
        except OSError:
            logger.exception(
                "Could not save message-cleanup state to %s.",
                self._tracking_path,
            )
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Could not remove temporary message-cleanup state file %s.",
                    temp_path,
                    exc_info=True,
                )

    def _discard(
        self,
        messages_by_chat: dict[int, set[int]],
        chat_id: int,
        message_id: int,
    ) -> None:
        with self._lock:
            messages = messages_by_chat.get(chat_id)
            if messages is None:
                return
            messages.discard(message_id)
            if not messages:
                messages_by_chat.pop(chat_id, None)
            self._persist_tracking_state()

    async def _delete_tracked(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        messages_by_chat: dict[int, set[int]],
        message_kind: str,
    ) -> bool:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "message to delete not found" in error_text:
                self._discard(messages_by_chat, chat_id, message_id)
                return True
            if "message can't be deleted" in error_text:
                logger.warning(
                    "Telegram no longer allows deleting %s message %s in chat %s.",
                    message_kind,
                    message_id,
                    chat_id,
                )
                self._discard(messages_by_chat, chat_id, message_id)
                return False
            logger.warning(
                "Could not delete %s message %s in chat %s; it will be retried.",
                message_kind,
                message_id,
                chat_id,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return False
        except TelegramAPIError:
            logger.warning(
                "Telegram API failed to delete %s message %s in chat %s; "
                "it will be retried.",
                message_kind,
                message_id,
                chat_id,
                exc_info=True,
            )
            return False
        except (ValueError, TypeError):
            logger.exception(
                "Invalid tracked %s message %s in chat %s; dropping it.",
                message_kind,
                message_id,
                chat_id,
            )
            self._discard(messages_by_chat, chat_id, message_id)
            return False
        self._discard(messages_by_chat, chat_id, message_id)
        return True

    async def safe_delete(self, bot: Bot, chat_id: int, message_id: int) -> bool:
        return await self._delete_tracked(
            bot,
            chat_id,
            message_id,
            self._tracked_messages,
            "bot",
        )

    async def safe_delete_user_message(
        self, bot: Bot, chat_id: int, message_id: int
    ) -> bool:
        return await self._delete_tracked(
            bot,
            chat_id,
            message_id,
            self._tracked_user_messages,
            "user",
        )

    async def clear_chat(
        self,
        bot: Bot,
        chat_id: int,
        *,
        preserve_message_ids: set[int] | None = None,
    ) -> None:
        preserved = preserve_message_ids or set()
        with self._lock:
            message_ids = list(self._tracked_messages.get(chat_id, set()))
        for message_id in message_ids:
            if message_id in preserved:
                continue
            await self.safe_delete(bot, chat_id, message_id)
        with self._lock:
            user_message_ids = list(self._tracked_user_messages.get(chat_id, set()))
        for message_id in user_message_ids:
            await self.safe_delete_user_message(bot, chat_id, message_id)

    def drop_chat(self, chat_id: int) -> None:
        with self._lock:
            self._tracked_messages.pop(chat_id, None)
            self._tracked_user_messages.pop(chat_id, None)
            self._persist_tracking_state()


message_service = MessageService()
