import pytest

from tests.test_catalog_handlers import app
from services.i18n import tr


def has_save(method):
    return any(button.callback_data=='acat:save' for row in method.reply_markup.inline_keyboard for button in row)


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['ru','uk','en'])
async def test_category_name_shows_save_and_persists_after_confirmation(app,language):
    await app.users.set_language(51,language)
    await app.users.set_admin(51,True)
    await app.callback('acat:addcategory')
    await app.text('Comedy <test>')
    assert has_save(app.session.calls[-1])
    assert '&lt;test&gt;' in app.session.calls[-1].text
    assert await app.catalog.repository.categories()==[]
    await app.callback('acat:save',message_id=len(app.session.calls)+100)
    rows=await app.catalog.repository.categories()
    assert len(rows)==1 and rows[0]['name']=='Comedy <test>' and rows[0]['position']==0


@pytest.mark.asyncio
async def test_category_edit_preserves_order_and_invalid_input_can_be_corrected(app):
    await app.users.set_admin(51,True)
    category=await app.catalog.repository.save_category('Original',17)
    await app.callback(f'acat:editcategory:{category}')
    await app.text('New name | bad')
    assert not has_save(app.session.calls[-1])
    await app.text('New name')
    assert has_save(app.session.calls[-1])
    await app.callback('acat:save',message_id=len(app.session.calls)+100)
    row=(await app.catalog.repository.categories())[0]
    assert row['name']=='New name' and row['position']==17
    await app.callback('acat:addcategory')
    await app.text('new NAME')
    assert app.session.calls[-1].text==tr('en','cat_category_exists')
    assert not has_save(app.session.calls[-1])
    await app.text('Second | 2')
    assert has_save(app.session.calls[-1])
    await app.callback('acat:save',message_id=len(app.session.calls)+100)
    assert len(await app.catalog.repository.categories())==2
