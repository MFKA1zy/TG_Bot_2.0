from types import SimpleNamespace

import pytest

from tests.test_catalog_handlers import app
import handlers.start as start
from services.i18n import tr


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['ru','uk','en'])
@pytest.mark.parametrize('channel',[None,'@test_channel'])
async def test_failed_subscription_check_always_sends_visible_feedback(app,monkeypatch,language,channel):
    await app.users.set_language(51,language)
    monkeypatch.setattr(start,'settings',SimpleNamespace(
        SUBSCRIPTION_REQUIRED=True,SUBSCRIPTION_CHANNEL_ID=channel,
        SUBSCRIPTION_CHANNEL_URL='https://t.me/test_channel' if channel else '',
    ))
    monkeypatch.setattr(start,'has_active_subscription',app.subscription)
    app.subscription.return_value=False
    for _ in range(2):
        begin=len(app.session.calls)
        await app.callback('check_subscription')
        calls=app.session.calls[begin:]
        assert calls[0].__api_method__=='answerCallbackQuery'
        assert calls[-1].__api_method__=='sendMessage'
        assert calls[-1].text==tr(language,'subscription_check_failed' if channel else 'subscription_not_configured')
        buttons=[button for row in calls[-1].reply_markup.inline_keyboard for button in row]
        assert any(button.callback_data=='check_subscription' for button in buttons)
        assert any(button.url=='https://t.me/test_channel' for button in buttons)==bool(channel)
