import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram import F

from tests.test_catalog_handlers import app
import handlers.admin.broadcast as ui
from services.broadcast_service import BroadcastService
from states.admin import AdminStates


def wire(app,monkeypatch):
    monkeypatch.setattr(ui,'repository',app.users)
    monkeypatch.setattr(ui,'broadcast_service',BroadcastService())
    monkeypatch.setattr(ui,'_broadcast_task',None)
    monkeypatch.setattr(ui,'_broadcast_launch_lock',asyncio.Lock())
    app.dp.callback_query.register(ui.admin_broadcast,F.data=='admin_broadcast')
    app.dp.callback_query.register(ui.confirm_broadcast,F.data=='admin_confirm_broadcast')
    app.dp.message.register(ui.receive_broadcast_text,AdminStates.waiting_broadcast_text)


@pytest.mark.asyncio
async def test_broadcast_preview_confirmation_delivery_and_history(app,monkeypatch):
    wire(app,monkeypatch)
    await app.users.set_admin(51,True)
    await app.users.ensure_user(52,language='en')
    async def delete(*args):
        assert app.session.calls[-1].__api_method__=='editMessageText'
        assert app.session.calls[-1].reply_markup.inline_keyboard[0][0].callback_data=='admin_confirm_broadcast'
        return False # Even failed cleanup must not stop a ready preview.
    monkeypatch.setattr(ui.message_service,'safe_delete_user_message',AsyncMock(side_effect=delete))
    await app.callback('admin_broadcast')
    await app.text('Test broadcast <plain text>')
    assert 'Recipients: 2' in app.session.calls[-1].text
    assert not await app.users.has_unfinished_broadcast()
    await app.callback('admin_confirm_broadcast')
    assert ui._broadcast_task is not None
    await ui._broadcast_task
    deliveries=[method for method in app.session.calls if method.__api_method__=='sendMessage' and method.text=='Test broadcast <plain text>']
    assert {method.chat_id for method in deliveries}=={51,52}
    assert all(method.parse_mode is None for method in deliveries)
    assert not await app.users.has_unfinished_broadcast()
    await app.callback('admin_confirm_broadcast')
    assert len([method for method in app.session.calls if method.__api_method__=='sendMessage' and method.text=='Test broadcast <plain text>'])==2


@pytest.mark.asyncio
async def test_ordinary_user_cannot_start_broadcast(app,monkeypatch):
    wire(app,monkeypatch)
    await app.callback('admin_broadcast')
    await app.callback('admin_confirm_broadcast')
    assert ui._broadcast_task is None
    assert not await app.users.has_unfinished_broadcast()
