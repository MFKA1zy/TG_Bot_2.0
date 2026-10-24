import asyncio

import pytest
from aiogram.exceptions import TelegramForbiddenError

from tests.test_catalog_handlers import app
import handlers.admin.reviews as moderation
from services.i18n import tr


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['ru','uk','en'])
async def test_warning_delivered_after_confirmation_once_and_localized(app,language):
    await app.users.ensure_user(52,language=language)
    await app.users.set_admin(51,True)
    await app.callback('mod:act:warn:52:0:0')
    reason='Please follow the rules: <b>no spam</b> & advertising.'
    await app.text(reason)
    prompt_id=len(app.session.calls)+100
    assert not [call for call in app.session.calls if call.__api_method__=='sendMessage' and call.chat_id==52]
    await asyncio.gather(app.callback('mod:commit',message_id=prompt_id),app.callback('mod:commit',message_id=prompt_id))
    sent=[call for call in app.session.calls if call.__api_method__=='sendMessage' and call.chat_id==52]
    assert len(sent)==1
    assert sent[0].text.startswith(tr(language,'rv_warning_received',text=''))
    assert '&lt;b&gt;no spam&lt;/b&gt; &amp;' in sent[0].text
    assert len((await moderation.service.repository.user_history(52))['warnings'])==1


@pytest.mark.asyncio
async def test_undeliverable_warning_is_saved_and_admin_informed(app,monkeypatch):
    await app.users.ensure_user(52,language='en')
    await app.users.set_admin(51,True)
    original=app.session.make_request
    async def fail(bot,method,timeout=None):
        if method.__api_method__=='sendMessage' and method.chat_id==52:
            raise TelegramForbiddenError(method=method,message='bot was blocked by the user')
        return await original(bot,method,timeout)
    monkeypatch.setattr(app.session,'make_request',fail)
    await app.callback('mod:act:warn:52:0:0')
    await app.text('Repeated advertising')
    await app.callback('mod:commit',message_id=len(app.session.calls)+100)
    assert 'could not be delivered' in app.session.calls[-1].text
    assert len((await moderation.service.repository.user_history(52))['warnings'])==1
    assert not (await app.users.get_user_by_telegram_id(52)).is_blocked


@pytest.mark.asyncio
async def test_warning_rechecks_permissions_before_saving_and_sending(app):
    await app.users.ensure_user(52,language='en')
    await app.users.set_admin(51,True)
    await app.callback('mod:act:warn:52:0:0')
    await app.text('A warning awaiting confirmation')
    prompt_id=len(app.session.calls)+100
    await app.users.set_admin(51,False)
    await app.callback('mod:commit',message_id=prompt_id)
    assert (await moderation.service.repository.user_history(52))['warnings']==[]
    assert not [call for call in app.session.calls if call.__api_method__=='sendMessage' and call.chat_id==52]
