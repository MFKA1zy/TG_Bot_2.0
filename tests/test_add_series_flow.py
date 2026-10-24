from datetime import datetime,timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message,Chat,User,Update,PhotoSize

from tests.test_catalog_handlers import app
from tests.test_catalog_phase2 import URL
from states.admin import AdminStates
import handlers.admin.series as ui


def wire(app,monkeypatch):
    monkeypatch.setattr(ui,'series_service',app.catalog.series)
    app.dp.callback_query.register(ui.admin_add_series,F.data=='admin_add_series')
    app.dp.callback_query.register(ui.admin_confirm_save,F.data=='admin_confirm_save')
    app.dp.callback_query.register(ui.skip_add_series_photo,F.data=='admin_skip_series_photo')
    app.dp.message.register(ui.admin_add_title,AdminStates.waiting_title)
    app.dp.message.register(ui.admin_add_url,AdminStates.waiting_url)
    app.dp.message.register(ui.receive_add_series_photo,AdminStates.waiting_series_photo,F.photo)
    app.dp.message.register(ui.reject_non_photo_series_image,AdminStates.waiting_series_photo)


def has_save(method):
    return any(button.callback_data=='admin_confirm_save' for row in method.reply_markup.inline_keyboard for button in row)


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['ru','uk','en'])
async def test_add_series_save_visible_after_url_without_photo(app,monkeypatch,language):
    wire(app,monkeypatch)
    await app.users.set_language(51,language)
    await app.users.set_admin(51,True)
    await app.callback('admin_add_series')
    await app.text('New <series>')
    await app.text(URL)
    previews=[call for call in app.session.calls if call.__api_method__=='editMessageText']
    assert has_save(previews[-1]) and '&lt;series&gt;' in previews[-1].text
    assert app.catalog.series.list_series()==[]
    await app.callback('admin_confirm_save',message_id=9999)
    assert app.catalog.series.list_series()==[]
    await app.callback('admin_confirm_save')
    items=app.catalog.series.list_series()
    assert len(items)==1 and items[0]['title']=='New <series>' and not items[0].get('photo_file_id')
    await app.callback('admin_confirm_save')
    assert len(app.catalog.series.list_series())==1


@pytest.mark.asyncio
async def test_invalid_title_url_and_photo_text_are_recoverable(app,monkeypatch):
    wire(app,monkeypatch)
    await app.users.set_admin(51,True)
    await app.callback('admin_add_series')
    await app.text('x'*257)
    await app.text('Correct title')
    await app.text('https://example.com/not-tiktok')
    await app.text(URL)
    await app.text('I do not have a photo')
    assert has_save([call for call in app.session.calls if call.__api_method__=='editMessageText'][-1])
    await app.callback('admin_confirm_save')
    assert len(app.catalog.series.list_series())==1


@pytest.mark.asyncio
@pytest.mark.parametrize('photo_fails',[False,True])
async def test_optional_poster_preview_fallback_caption_and_save(app,monkeypatch,photo_fails):
    wire(app,monkeypatch)
    await app.users.set_admin(51,True)
    upload=AsyncMock(return_value='https://i.ibb.co/test/poster.jpg')
    monkeypatch.setattr(ui,'_upload_telegram_photo',upload)
    original=app.session.make_request
    photo_response={}
    async def request(bot,method,timeout=None):
        if method.__api_method__=='sendPhoto':
            assert len(method.caption.encode('utf-16-le'))<=2048
            if photo_fails:
                raise TelegramBadRequest(method=method,message='photo unavailable')
        result=await original(bot,method,timeout)
        if method.__api_method__=='sendPhoto':
            photo_response['id']=result.message_id
        return result
    monkeypatch.setattr(app.session,'make_request',request)
    await app.callback('admin_add_series')
    await app.text('Series with poster')
    await app.text(URL+'?extra='+'a'*1200)
    photo=Message(message_id=900,date=datetime.now(timezone.utc),chat=Chat(id=51,type='private'),
        from_user=User(id=51,is_bot=False,first_name='User'),
        photo=[PhotoSize(file_id='poster-file',file_unique_id='unique',width=100,height=100,file_size=100)])
    await app.dp.feed_update(app.bot,Update(update_id=900,message=photo))
    await app.callback('admin_confirm_save',message_id=photo_response.get('id',10))
    items=app.catalog.series.list_series()
    assert len(items)==1 and items[0]['photo_url']=='https://i.ibb.co/test/poster.jpg'
    upload.assert_awaited_once()
