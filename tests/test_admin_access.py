from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

import handlers.admin.panel as admin_panel
from database.connection import init_db
from database.repository import UserRepository


def _message(user_id: int, chat_type: str = "private") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
    )


@pytest.mark.asyncio
async def test_admin_access_requires_private_chat_and_database_permission(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "users.db"
    await init_db(db_path)
    repository = UserRepository(str(db_path))
    monkeypatch.setattr(admin_panel, "repository", repository)
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)

    ordinary_user = _message(51)
    await repository.ensure_user(51)
    assert await admin_panel.ensure_admin(ordinary_user) is False
    answer.assert_awaited_once()

    await repository.set_admin(51, True)
    assert await admin_panel.ensure_admin(ordinary_user) is True

    group_message = _message(51, "group")
    assert await admin_panel.ensure_admin(group_message) is False
    assert answer.await_count == 2
