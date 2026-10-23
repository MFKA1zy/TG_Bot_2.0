from datetime import datetime,timezone
from types import SimpleNamespace
import json
import logging
import sqlite3
import subprocess
import sys

import pytest
from aiogram.types import InlineQuery,Update,User

from tests.test_catalog_handlers import app
from tests.test_catalog_phase2 import URL
import handlers.start as start
import handlers.captcha as captcha
import handlers.inline_search as inline
import handlers.reviews as reviews
import handlers.roulette as roulette
from database.connection import init_db
from database.economy_repository import EconomyRepository
from services.captcha_service import CaptchaService
from services.i18n import tr
from services.logging_security import SecretFormatter
from tools.migration_preflight import run
from tools.run_staging import isolated_environment
from services.lifecycle import drain_tasks
from unittest.mock import AsyncMock
import asyncio
import os
from pathlib import Path
from services.access_service import has_active_subscription as live_membership
import services.access_service as access
from tools.snapshot_data import snapshot
from services.fsm_storage import PruningMemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext


@pytest.mark.asyncio
@pytest.mark.parametrize('language',['ru','uk','en'])
async def test_complete_registration_catalog_reviews_economy_roulette_restart(app,tmp_path,monkeypatch,language):
    async with roulette.roulette.connection(True) as db:
        await db.execute('UPDATE users SET language=NULL,captcha_passed=0 WHERE telegram_id=51')
    answers=tmp_path/'captcha.json'
    assets=tmp_path/'assets'
    assets.mkdir()
    (assets/'1.png').write_bytes(b'test image; Telegram upload is stubbed')
    answers.write_text(json.dumps({'1':'12345'}))
    monkeypatch.setattr(captcha,'captcha_service',CaptchaService(assets,answers))
    monkeypatch.setattr(start,'settings',SimpleNamespace(SUBSCRIPTION_REQUIRED=True,SUBSCRIPTION_CHANNEL_URL='https://t.me/test_channel'))
    monkeypatch.setattr(start,'has_active_subscription',app.subscription)
    app.subscription.return_value=False
    await app.text('/start')
    assert app.session.calls[-1].reply_markup.inline_keyboard[0][0].callback_data=='set_language:ru'
    await app.callback('set_language:'+language)
    assert not (await app.users.get_user_by_telegram_id(51)).captcha_passed
    await app.text('12345')
    assert (await app.users.get_user_by_telegram_id(51)).captcha_passed
    assert any(button.callback_data=='check_subscription' for row in app.session.calls[-1].reply_markup.inline_keyboard for button in row)
    app.subscription.return_value=True
    await app.callback('check_subscription')
    assert any(button.callback_data=='cat:home' for row in app.session.calls[-1].reply_markup.inline_keyboard for button in row)
    category=await app.catalog.repository.save_category('Drama')
    app.catalog.series.add_series('Series <tag>',URL,alternative_titles=['Alternate'],category_ids=[category],episodes=[{'title':'Episode 1','url':URL}])
    await app.callback('cat:home')
    await app.callback('cat:categories:0')
    await app.callback(f'cat:list:categories:0:{category}')
    await app.callback(f'cat:card:1:categories:0:{category}')
    assert '&lt;tag&gt;' in app.session.calls[-1].text
    await app.callback('cat:watch:1:0:all:0:0')
    assert any(button.url==URL for row in app.session.calls[-1].reply_markup.inline_keyboard for button in row)
    await app.callback('cat:confirm:1:all:0:0')
    await app.callback('cat:confirm:1:all:0:0')
    await app.callback('rev:score:1:8:all:0:0')
    await app.callback('rev:score:1:9:all:0:0')
    assert (await reviews.service.repository.rating_summary(1,51))['own']==9
    await app.callback('rev:write:1:all:0:0')
    await app.text('A thoughtful review with useful details.')
    row=await reviews.service.repository.own_review(51,1)
    assert row['status']=='pending'
    await app.users.set_admin(51,True)
    await app.callback(f"mod:act:published:{row['id']}:{row['version']}:0")
    await app.text('Approved after inspection')
    await app.callback('mod:commit',message_id=len(app.session.calls)+100)
    assert (await reviews.service.repository.review(row['id']))['status']=='published'
    await app.callback('pf:home')
    assert tr(language,'pt_history') in str(app.session.calls[-1].reply_markup)
    await app.callback('pf:items:watched:0')
    await app.callback('pf:history:0')
    await app.callback('pf:leaders:0')
    monkeypatch.setattr(roulette.roulette,'randbelow',lambda n:75)
    await app.callback('rr:home')
    await app.callback('rr:spin')
    await app.callback('rr:spin')
    await app.callback('rr:history:0')
    assert (await roulette.roulette.status(51))['spins']==1
    await init_db(app.users.db_path)
    fresh=EconomyRepository(app.users.db_path)
    assert (await fresh.profile(51))['points_balance']==30
    assert len((await fresh.history(51))[0])==2
    assert (await fresh.leaderboard(51))[3]['rank']==1
    await app.users.set_user_blocked(51,True)
    await app.callback('rr:spin')
    await app.callback('cat:confirm:1:all:0:0')
    assert (await fresh.profile(51))['points_balance']==30


def test_migration_preflight_on_legacy_copy_preserves_source_and_rejects_reuse(tmp_path):
    source=tmp_path/'source'
    source.mkdir()
    path=source/'users.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,telegram_id INTEGER UNIQUE,username TEXT,first_name TEXT,last_name TEXT,is_admin INTEGER,captcha_passed INTEGER,created_at TEXT,last_activity TEXT,is_blocked INTEGER)')
        db.execute("INSERT INTO users VALUES (7,51,'old','Old',NULL,1,1,'2020-01-01',NULL,0)")
    series={'series':[{'id':17,'title':'Old series','tiktok_url':URL,'photo_file_id':'poster-id'}]}
    (source/'series.json').write_text(json.dumps(series))
    (source/'settings.json').write_text('{"custom":42}')
    original=path.read_bytes()
    output=tmp_path/'result'
    report=run(path,source,output)
    assert report['existing_rows_preserved'] and report['migrations_run']==2
    assert report['series_after']==1
    assert path.read_bytes()==original
    assert json.loads((source/'series.json').read_text())==series
    with pytest.raises(ValueError):
        run(path,source,output)
    with pytest.raises(ValueError):
        run(path,source,source/'child')
    with pytest.raises(ValueError):
        snapshot(path,source,tmp_path/'backup')
    manifest=snapshot(path,source,tmp_path/'backup',writers_stopped=True)
    assert manifest['integrity']=='ok' and manifest['counts']['users']==1
    assert (tmp_path/'backup'/'settings.json').read_bytes()==(source/'settings.json').read_bytes()


def test_log_redaction_covers_message_traceback_proxy_and_keys():
    formatter=SecretFormatter('%(message)s',secrets=['imgbb-secret','activation-secret'])
    try:
        raise RuntimeError('imgbb-secret activation-secret https://user:password@proxy.local')
    except RuntimeError:
        record=logging.LogRecord('test',logging.ERROR,__file__,1,'URL bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890',(),sys.exc_info())
    text=formatter.format(record)
    for value in ['imgbb-secret','activation-secret','password','ABCDEFGHIJKLMNOPQRSTUVWXYZ']:
        assert value not in text
    assert 'RuntimeError' in text and '[REDACTED]' in text


def test_snapshot_reads_committed_wal_without_copying_open_database(tmp_path):
    source=tmp_path/'source'
    source.mkdir()
    path=source/'users.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT)')
        db.commit()
        db.execute("INSERT INTO users VALUES (51,'in WAL')")
        db.commit()
        assert Path(str(path)+'-wal').exists()
        report=snapshot(path,source,tmp_path/'snapshot',writers_stopped=True)
        assert report['counts']['users']==1
        with sqlite3.connect(tmp_path/'snapshot'/'users.db') as copy:
            assert copy.execute('SELECT name FROM users').fetchone()[0]=='in WAL'


def test_staging_environment_excludes_production_paths_and_inherited_secrets(tmp_path,monkeypatch):
    import tools.run_staging as launcher
    monkeypatch.setattr(launcher,'ROOT',tmp_path)
    (tmp_path/'.env').write_text('BOT_TOKEN=123:PRODUCTION\nIMGBB_API_KEY=secret\n')
    (tmp_path/'.env.example').write_text('BOT_TOKEN=\nIMGBB_API_KEY=\n')
    env_file=tmp_path/'.env.test'
    monkeypatch.setenv('IMGBB_API_KEY','production-key')
    env_file.write_text(f'BOT_TOKEN=456:TEST\nDATA_DIR={tmp_path / "isolated"}\n')
    env=isolated_environment(env_file)
    assert env['BOT_TOKEN']=='456:TEST' and 'IMGBB_API_KEY' not in env
    assert env['DB_PATH']==str(tmp_path/'isolated'/'users.db')
    env_file.write_text('BOT_TOKEN=123:PRODUCTION\n')
    with pytest.raises(ValueError):
        isolated_environment(env_file)
    env_file.write_text(f'BOT_TOKEN=456:TEST\nDATA_DIR={tmp_path/"data"}\n')
    with pytest.raises(ValueError):
        isolated_environment(env_file)


def test_explicit_environment_file_does_not_load_production_defaults(tmp_path):
    env_file=tmp_path/'test.env'
    env_file.write_text('BOT_TOKEN=12345:TEST\nSUBSCRIPTION_REQUIRED=true\nSUBSCRIPTION_CHANNEL_ID=@isolated\n')
    env={key:value for key,value in os.environ.items() if key not in {'BOT_TOKEN','SUBSCRIPTION_REQUIRED','SUBSCRIPTION_CHANNEL_ID','CHANNEL_ID','TELEGRAM_CHANNEL_ID'}}
    env['ENV_FILE']=str(env_file)
    script="from config import settings; assert settings.BOT_TOKEN=='12345:TEST'; assert settings.SUBSCRIPTION_REQUIRED; assert settings.SUBSCRIPTION_CHANNEL_ID=='@isolated'"
    result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,timeout=10)
    assert result.returncode==0


@pytest.mark.asyncio
async def test_inline_live_access_search_pages_and_unsubscribe(app,monkeypatch):
    monkeypatch.setattr(inline,'repository',app.users)
    monkeypatch.setattr(inline,'series_service',app.catalog.series)
    monkeypatch.setattr(inline,'CatalogRepository',lambda:app.catalog.repository)
    monkeypatch.setattr(inline,'settings',SimpleNamespace(SUBSCRIPTION_REQUIRED=True))
    monkeypatch.setattr(access,'settings',SimpleNamespace(SUBSCRIPTION_REQUIRED=True,SUBSCRIPTION_CHANNEL_ID='@test'))
    monkeypatch.setattr(inline,'has_active_subscription',live_membership)
    bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status='member')))
    for i in range(55):
        app.catalog.series.add_series(f'Series {i}',URL,alternative_titles=[f'Alias {i}'],photo_url='https://i.ibb.co/test/poster.jpg')
    async def query(text,offset=''):
        answer=AsyncMock()
        await inline.search_inline(SimpleNamespace(from_user=SimpleNamespace(id=51,language_code='en'),bot=bot,query=text,offset=offset,answer=answer))
        return answer.await_args.kwargs
    first=await query('')
    assert len(first['results'])==50 and first['next_offset']=='50'
    assert first['cache_time']==0 and first['is_personal']
    assert len((await query('','50'))['results'])==5
    for text in ('Series 42','Alias 42','Sereis 42'):
        result=await query(text)
        assert any(row.title=='Series 42' for row in result['results'])
        assert 'poster.jpg' in result['results'][0].thumbnail_url
    assert len((await query('zzzznonexistentzzzz'))['results'])==1
    checks=bot.get_chat_member.await_count
    bot.get_chat_member.return_value=SimpleNamespace(status='left')
    assert (await query('Series 42'))['results']==[]
    assert bot.get_chat_member.await_count==checks+1
    bot.get_chat_member.return_value=SimpleNamespace(status='member')
    await app.users.set_captcha_passed(51,False)
    assert (await query('Series 42'))['results']==[]
    await app.users.set_captcha_passed(51,True)
    await app.users.set_user_blocked(51,True)
    assert (await query('Series 42'))['results']==[]


@pytest.mark.asyncio
async def test_shutdown_drains_commits_and_cancels_unfinished_transaction(app):
    repo=roulette.roulette
    started=asyncio.Event()
    async def write_then_wait():
        async with repo.connection(True) as db:
            await db.execute('UPDATE users SET points_balance=123 WHERE telegram_id=51')
            started.set()
            await asyncio.Event().wait()
    pending=asyncio.create_task(write_then_wait())
    await started.wait()
    finished=[]
    async def quick():
        finished.append(True)
    await drain_tasks({pending,asyncio.create_task(quick())},timeout=.01)
    assert pending.cancelled() and finished
    assert (await repo.profile(51))['points_balance']==0


@pytest.mark.asyncio
async def test_fsm_completed_and_read_only_sessions_do_not_accumulate():
    storage=PruningMemoryStorage()
    for uid in range(2000):
        key=StorageKey(bot_id=1,chat_id=uid,user_id=uid)
        assert await storage.get_state(key) is None
        assert await storage.get_data(key)=={}
        assert await storage.get_value(key,'missing',42)==42
        context=FSMContext(storage,key)
        await context.set_state('input')
        await context.update_data(prompt_id=123)
        assert await context.get_state()=='input'
        assert await context.get_data()=={'prompt_id':123}
        await context.clear()
    assert len(storage.storage)==0
    await storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed',[False,True])
async def test_process_crash_preserves_atomicity_and_replay(app,tmp_path,committed):
    env_file=tmp_path/'empty.env'
    env_file.write_text('')
    script='''
import asyncio,os,sys
from database.roulette_repository import RouletteRepository
async def run():
    repo=RouletteRepository(sys.argv[1],randbelow=lambda n:75)
    if sys.argv[2]=='False':
        original=repo._post
        async def crash(*args,**kwargs):
            await original(*args,**kwargs)
            os._exit(7)
        repo._post=crash
    await repo.spin(51,'crash-test')
    os._exit(7)
asyncio.run(run())
'''
    env=os.environ.copy()
    env.update(ENV_FILE=str(env_file),BOT_TOKEN='')
    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,str(app.users.db_path),str(committed)],env=env,capture_output=True,timeout=20)
    assert result.returncode==7
    status=await roulette.roulette.status(51)
    assert status['spins']==int(committed) and status['balance']==20*int(committed)
    result=await roulette.roulette.spin(51,'crash-test')
    assert result['replayed']==committed
    assert (await roulette.roulette.status(51))['spins']==1
