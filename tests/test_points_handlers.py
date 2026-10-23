import pytest

from tests.test_catalog_handlers import app
from tests.test_catalog_phase2 import URL
import handlers.profile as profile_ui


@pytest.mark.asyncio
async def test_profile_navigation_view_award_and_repeat(app):
    app.catalog.series.add_series("Series",URL)
    await app.callback("pf:home")
    assert "Balance: 0" in app.session.calls[-1].text
    for key in ("watched","favorites","ratings","reviews"):
        await app.callback(f"pf:items:{key}:0")
        assert "No entries" in app.session.calls[-1].text
    await app.callback("cat:card:1:all:0:0")
    await app.callback("cat:view:1:all:0:0")
    assert (await profile_ui.economy.profile(51))["points_balance"]==0
    await app.callback("cat:confirm:1:all:0:0")
    assert "+10 points" in app.session.calls[-1].text
    await app.callback("cat:confirm:1:all:0:0")
    assert "No additional points" in app.session.calls[-1].text
    await app.callback("pf:home")
    assert "Balance: 10" in app.session.calls[-1].text
    await app.callback("pf:history:0")
    assert "+10" in app.session.calls[-1].text and "First viewing" in app.session.calls[-1].text
    await app.callback("pf:leaders:0")
    assert "User" in app.session.calls[-1].text


@pytest.mark.asyncio
@pytest.mark.parametrize("reason",["blocked","subscription","captcha"])
async def test_denied_users_cannot_receive_points_or_read_profile(app,reason):
    app.catalog.series.add_series("Series",URL)
    if reason=="blocked":
        await app.users.set_user_blocked(51,True)
    elif reason=="captcha":
        await app.users.set_captcha_passed(51,False)
    else:
        app.subscription.return_value=False
    await app.callback("cat:confirm:1:all:0:0")
    await app.callback("pf:home")
    assert (await profile_ui.economy.profile(51))["points_balance"]==0
    assert await app.catalog.repository.viewed_ids(51)==set()


@pytest.mark.asyncio
async def test_admin_credit_debit_reasons_confirmation_and_permissions(app):
    await app.callback("ec:home")
    assert "administrator panel" in app.session.calls[-1].text
    await app.users.set_admin(51,True)
    await app.callback("ec:adjust:51:credit")
    await app.text("100")
    await app.text("Competition reward <winner>")
    assert (await profile_ui.economy.profile(51))["points_balance"]==0
    await app.callback("ec:commit",message_id=len(app.session.calls)+100)
    assert (await profile_ui.economy.profile(51))["points_balance"]==100
    await app.callback("ec:adjust:51:debit")
    await app.text("150")
    await app.text("Too much")
    await app.callback("ec:commit",message_id=len(app.session.calls)+100)
    assert "Insufficient" in app.session.calls[-1].text
    assert (await profile_ui.economy.profile(51))["points_balance"]==100
    await app.callback("ec:history:51:0")
    assert "&lt;winner&gt;" in app.session.calls[-1].text
    assert "administrator: 51" in app.session.calls[-1].text


@pytest.mark.asyncio
async def test_admin_settings_stale_confirmation_and_revoked_role(app):
    await app.users.set_admin(51,True)
    await app.callback("ec:reward")
    await app.text("25")
    await app.callback("ec:commit",message_id=9999)
    assert (await profile_ui.economy.settings())["view_reward"]==10
    await app.callback("ec:reward")
    await app.text("25")
    await app.callback("ec:commit",message_id=len(app.session.calls)+100)
    assert (await profile_ui.economy.settings())["view_reward"]==25
    await app.callback("ec:adjust:51:credit")
    await app.text("20")
    await app.text("Pending grant")
    prompt=len(app.session.calls)+100
    await app.users.set_admin(51,False)
    await app.callback("ec:commit",message_id=prompt)
    assert (await profile_ui.economy.profile(51))["points_balance"]==0


@pytest.mark.asyncio
async def test_legacy_preview_has_no_effect_until_confirmation(app):
    await app.users.set_admin(51,True)
    async with profile_ui.economy.connection(True) as db:
        await db.execute("INSERT INTO confirmed_views(user_id,series_id,confirmed_at) VALUES (51,7,'2025-01-01')")
    await app.callback("ec:legacy")
    assert "PREVIEW" in app.session.calls[-1].text
    prompt_id=app.session.calls[-1].message_id
    assert (await profile_ui.economy.profile(51))["points_balance"]==0
    await app.callback("ec:commit",message_id=prompt_id)
    assert (await profile_ui.economy.profile(51))["points_balance"]==10
    await app.callback("ec:legacy")
    assert "Legacy views: 0" in app.session.calls[-1].text


@pytest.mark.asyncio
@pytest.mark.parametrize("language,expected",[("ru","Баланс"),("uk","Баланс"),("en","Balance")])
async def test_profile_preserves_language_and_escapes_names(app,language,expected):
    await app.users.ensure_user(51,first_name="<b>Profile</b>",username="tester")
    await app.users.set_language(51,language)
    await app.callback("pf:home")
    assert expected in app.session.calls[-1].text
    assert "&lt;b&gt;Profile&lt;/b&gt;" in app.session.calls[-1].text
    assert "@tester" in app.session.calls[-1].text


@pytest.mark.asyncio
async def test_profile_lists_use_actual_owner_and_handle_archived_series(app):
    app.catalog.series.add_series("Series",URL)
    await app.catalog.confirm_view_reward(51,1)
    app.catalog.series.delete_series(1)
    await app.callback("pf:items:watched:999")
    assert "Series unavailable" in app.session.calls[-1].text
    await app.callback("pf:history:999")
    assert "Series" in app.session.calls[-1].text
    await app.callback("pf:history:52:0")  # forged extra data never selects a different owner
    assert "+10" in app.session.calls[-1].text
