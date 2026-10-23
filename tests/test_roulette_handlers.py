import pytest
from aiogram.exceptions import TelegramBadRequest

from tests.test_catalog_handlers import app
import handlers.roulette as ui
import handlers.admin.roulette as admin_ui


@pytest.mark.asyncio
async def test_spin_profile_history_ack_and_forgery(app,monkeypatch):
    monkeypatch.setattr(ui.roulette,'randbelow',lambda n:75)
    await app.callback('rr:home')
    assert 'Available spins: 1' in app.session.calls[-1].text
    start=len(app.session.calls)
    await app.callback('rr:spin')
    calls=app.session.calls[start:]
    assert calls[0].__api_method__=='answerCallbackQuery'
    assert '+20 points' in calls[-1].text
    await app.callback('rr:spin')
    assert 'already used' in app.session.calls[-1].text
    await app.callback('rr:spin:52')
    assert 'Invalid' in app.session.calls[-1].text
    assert (await ui.roulette.status(51))['spins']==1
    await app.callback('pf:home')
    assert 'Spins: 1' in app.session.calls[-1].text
    assert 'Winnings: 20' in app.session.calls[-1].text
    await app.callback('pf:history:0')
    assert 'Roulette prize' in app.session.calls[-1].text
    await app.callback('rr:history:999')
    assert '+20' in app.session.calls[-1].text


@pytest.mark.asyncio
@pytest.mark.parametrize('reason',['captcha','blocked','subscription'])
async def test_protected_spin(app,reason):
    if reason=='captcha':
        await app.users.set_captcha_passed(51,False)
    elif reason=='blocked':
        await app.users.set_user_blocked(51,True)
    else:
        app.subscription.return_value=False
    await app.callback('rr:spin')
    assert (await ui.roulette.status(51))['spins']==0


@pytest.mark.asyncio
async def test_telegram_failure_does_not_repeat_award(app,monkeypatch):
    monkeypatch.setattr(ui.roulette,'randbelow',lambda n:99)
    original=app.session.make_request
    async def failure(bot,method,timeout=None):
        if method.__api_method__=='editMessageText':
            raise TelegramBadRequest(method=method,message='message unavailable')
        return await original(bot,method,timeout)
    monkeypatch.setattr(app.session,'make_request',failure)
    await app.callback('rr:spin')
    assert app.session.calls[-1].__api_method__=='sendMessage'
    assert '+100 points' in app.session.calls[-1].text
    assert (await ui.roulette.status(51))['winnings']==100
    assert (await ui.roulette.spin(51,'callback:1'))['replayed']
    assert (await ui.roulette.status(51))['spins']==1


@pytest.mark.asyncio
async def test_admin_confirmation_prizes_stats_user_and_revoked_role(app):
    await app.callback('ra:home')
    assert 'administrator panel' in app.session.calls[-1].text
    await app.users.set_admin(51,True)
    await app.callback('ra:home')
    await app.callback('ra:prizes')
    await app.text('+ | 7 | 99 | 1')
    assert (await ui.roulette.config())['version']==1
    await app.text('+ | 7 | 100 | 1')
    # Fake Telegram assigns this message ID from the number of calls.
    prompt_id=len(app.session.calls)+100
    await app.callback('ra:commit',message_id=prompt_id)
    config=await ui.roulette.config()
    assert config['prizes'][0]['points']==7 and len(config['prizes'])==1
    await app.callback('rr:spin')
    await app.callback('ra:stats')
    assert '+7: 1' in app.session.calls[-1].text
    await app.callback('ra:user')
    await app.text('51')
    await app.callback('ra:history:51:0')
    assert '+7' in app.session.calls[-1].text
    await app.callback('ra:toggle')
    assert (await ui.roulette.config())['enabled']==1
    await app.users.set_admin(51,False)
    await app.callback('ra:commit')
    assert (await ui.roulette.config())['enabled']==1


@pytest.mark.asyncio
async def test_admin_interval_toggle_and_stale_confirmation(app):
    await app.users.set_admin(51,True)
    await app.callback('ra:interval')
    await app.text('12')
    prompt_id=len(app.session.calls)+100
    await app.callback('ra:commit',message_id=prompt_id)
    assert (await ui.roulette.config())['cooldown_seconds']==43200
    await app.callback('ra:toggle')
    await app.callback('ra:commit',message_id=9999)
    assert (await ui.roulette.config())['enabled']==1
    await app.callback('ra:toggle')
    await app.callback('ra:commit')
    assert (await ui.roulette.config())['enabled']==0
    await app.callback('rr:spin')
    assert 'disabled' in app.session.calls[-1].text
