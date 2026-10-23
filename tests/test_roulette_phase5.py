import asyncio
from datetime import datetime,timedelta,timezone
import sqlite3

import pytest

from database.connection import init_db
from database.economy_repository import EconomyError
from database.roulette_repository import RouletteRepository,RouletteError,validate_prizes,weighted_prize
from database.roulette_schema import DEFAULT_PRIZES
from tests.test_points_phase4 import app
import database.roulette_schema as schema
from database.repository import UserRepository
from unittest.mock import AsyncMock


def repository(app,draw=75):
    now=[datetime(2026,10,24,tzinfo=timezone.utc)]
    return RouletteRepository(app.users.db_path,clock=lambda:now[0],randbelow=lambda _:draw),now


@pytest.mark.parametrize('draw,expected',[(0,0),(19,0),(20,5),(49,5),(50,10),(74,10),(75,20),(89,20),(90,50),(97,50),(98,100),(99,100)])
def test_weighted_boundaries(draw,expected):
    prizes=[dict(id=i+1,points=p,weight=w,enabled=True) for i,(p,w) in enumerate(DEFAULT_PRIZES)]
    assert weighted_prize(prizes,lambda n:draw)['points']==expected


@pytest.mark.parametrize('prizes', [[],[dict(points=1,weight=99,enabled=True)],
    [dict(points=-1,weight=100,enabled=True)], [dict(points=1,weight=0,enabled=True)],
    [dict(points=1,weight=100,enabled=False)], [dict(points=1,weight=-1,enabled=True)],
    [dict(points=1,weight=50,enabled=True)]*2,[dict(points=True,weight=100,enabled=True)],
    [dict(points=1,weight=100.0,enabled=True)]])
def test_invalid_configuration(prizes):
    with pytest.raises(RouletteError,match='config'):
        validate_prizes(prizes)


@pytest.mark.asyncio
@pytest.mark.parametrize('draw,amount',[(0,0),(75,20),(99,100)])
async def test_first_spin_ledger_balance_history_and_restart(app,draw,amount):
    repo,now=repository(app,draw)
    result=await repo.spin(51,'first')
    assert result['awarded_points']==amount and not result['replayed']
    assert result['remaining']==86400
    profile=await repo.profile(51)
    assert profile['points_balance']==profile['points_earned']==amount
    assert profile['points_spent']==0
    ledger,_,_=await repo.history(51)
    assert ledger[0]['transaction_id']==result['transaction_id']
    entries,_,_=await app.economy.history(51)
    assert entries[0]['operation']=='roulette_reward' and entries[0]['amount']==amount
    await init_db(repo.db_path)
    restarted=RouletteRepository(repo.db_path,clock=lambda:now[0])
    assert (await restarted.spin(51,'first'))['replayed']
    with pytest.raises(RouletteError,match='cooldown'):
        await restarted.spin(51,'new')
    assert (await restarted.status(51))['spins']==1


@pytest.mark.asyncio
async def test_exact_boundary_replay_after_cooldown_and_owner(app):
    repo,now=repository(app)
    first=await repo.spin(51,'original')
    now[0]+=timedelta(seconds=86399,microseconds=999999)
    with pytest.raises(RouletteError,match='cooldown') as error:
        await repo.spin(51,'early')
    assert error.value.remaining==1
    now[0]+=timedelta(microseconds=1)
    assert (await repo.spin(51,'original'))['id']==first['id']
    assert (await repo.status(51))['spins']==1
    assert not (await repo.spin(51,'second'))['replayed']
    now[0]+=timedelta(days=2)
    assert (await repo.spin(51,'original'))['id']==first['id']
    with pytest.raises(RouletteError,match='invalid'):
        await repo.spin(52,'original')
    assert (await repo.status(51))['winnings']==40


@pytest.mark.asyncio
@pytest.mark.parametrize('same',[True,False])
async def test_concurrent_callbacks_at_most_one_award(app,same):
    repo,_=repository(app)
    results=await asyncio.gather(*(repo.spin(51,'same' if same else str(i)) for i in range(6)),return_exceptions=True)
    assert sum(isinstance(row,dict) and not row['replayed'] for row in results)==1
    assert (await repo.status(51))['spins']==1
    assert (await repo.profile(51))['points_balance']==20
    if same:
        assert all(isinstance(row,dict) for row in results)
    else:
        assert all(isinstance(row,dict) or str(row)=='cooldown' for row in results)


@pytest.mark.asyncio
@pytest.mark.parametrize('table',['roulette_spins','points_ledger','users'])
async def test_rollback_all_writes_on_failure(app,table):
    repo,_=repository(app)
    operation='UPDATE' if table=='users' else 'INSERT'
    async with repo.connection(True) as db:
        await db.execute(f"CREATE TRIGGER fail BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        await repo.spin(51,'failed')
    assert (await repo.status(51))['spins']==0
    assert (await repo.profile(51))['points_balance']==0
    assert (await app.economy.history(51))[0]==[]
    async with repo.connection(True) as db:
        await db.execute('DROP TRIGGER fail')
    assert not (await repo.spin(51,'failed'))['replayed']


@pytest.mark.asyncio
async def test_config_history_disable_interval_and_stale(app):
    repo,now=repository(app)
    await repo.spin(51,'before')
    config=await repo.config()
    await repo.save_config(99,config['version'],prizes=[dict(points=7,weight=100,enabled=True)],cooldown_seconds=3600)
    assert (await repo.history(51))[0][0]['awarded_points']==20
    assert (await repo.status(51))['remaining']==86400
    with pytest.raises(RouletteError,match='stale'):
        await repo.save_config(99,config['version'],enabled=False)
    config=await repo.config()
    await repo.save_config(99,config['version'],enabled=False)
    with pytest.raises(RouletteError,match='disabled'):
        await repo.spin(52,'disabled')
    config=await repo.config()
    await repo.save_config(99,config['version'],enabled=True)
    assert (await repo.spin(52,'enabled'))['awarded_points']==7
    assert (await repo.status(52))['remaining']==3600
    assert (await repo.statistics())['points']==27
    async with repo.connection() as db:
        assert (await repo.one(db,'SELECT COUNT(*) AS n FROM roulette_prizes WHERE deleted=1'))['n']==6
        assert await repo.one(db,'PRAGMA foreign_key_check') is None


@pytest.mark.asyncio
async def test_fresh_permissions_and_leaderboard(app):
    repo,_=repository(app)
    with pytest.raises(EconomyError,match='access'):
        await repo.save_config(51,1,enabled=False)
    await app.users.set_captcha_passed(51,False)
    with pytest.raises(EconomyError,match='access'):
        await repo.spin(51,'captcha')
    await app.users.set_captcha_passed(51,True)
    await app.users.set_user_blocked(51,True)
    with pytest.raises(EconomyError,match='access'):
        await repo.spin(51,'blocked')
    await app.users.set_user_blocked(51,False)
    await repo.spin(51,'allowed')
    rows,_,_,_,_=await repo.leaderboard(51)
    assert rows[0]['first_name']=='User 51' and rows[0]['points']==20


@pytest.mark.asyncio
async def test_bounded_busy_and_retry(app,monkeypatch):
    repo,_=repository(app)
    original=repo._spin
    attempts=[]
    async def busy_once(*args):
        attempts.append(1)
        if len(attempts)==1:
            error=sqlite3.OperationalError('busy')
            error.sqlite_errorcode=sqlite3.SQLITE_BUSY
            raise error
        return await original(*args)
    monkeypatch.setattr(repo,'_spin',busy_once)
    await repo.spin(51,'retry')
    assert len(attempts)==2 and (await repo.status(51))['spins']==1
    async def busy(*args):
        error=sqlite3.OperationalError('busy')
        error.sqlite_errorcode=sqlite3.SQLITE_BUSY
        raise error
    monkeypatch.setattr(repo,'_spin',busy)
    with pytest.raises(RouletteError,match='busy'):
        await repo.spin(52,'busy')
    assert (await repo.status(52))['spins']==0


@pytest.mark.asyncio
async def test_migration_preserves_phase4_rows_sequence_indexes_backup(tmp_path,monkeypatch):
    path=tmp_path/'data'/'users.db'
    original=schema.create_roulette_tables
    monkeypatch.setattr(schema,'create_roulette_tables',AsyncMock())
    await init_db(path)
    users=UserRepository(path)
    for uid in (51,99):
        await users.ensure_user(uid,language='en')
        await users.set_captcha_passed(uid,True)
    await users.set_admin(99,True)
    repo=RouletteRepository(path,randbelow=lambda n:75)
    await repo.adjust(99,51,42,'old award','old')
    async with repo.connection(True) as db:
        await db.execute("UPDATE sqlite_sequence SET seq=500 WHERE name='points_ledger'")
        before=await repo.one(db,'SELECT * FROM points_ledger')
    monkeypatch.setattr(schema,'create_roulette_tables',original)
    await init_db(path)
    await init_db(path)
    backups=list(path.parent.glob('users.db.pre-migration.*.bak'))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('SELECT points_balance FROM users WHERE telegram_id=51').fetchone()[0]==42
        assert db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]==4
    async with repo.connection() as db:
        assert await repo.one(db,'SELECT * FROM points_ledger')==before
        assert (await repo.one(db,"SELECT seq FROM sqlite_sequence WHERE name='points_ledger'"))['seq']==500
        assert (await repo.one(db,'PRAGMA integrity_check'))['integrity_check']=='ok'
        assert await repo.one(db,'PRAGMA foreign_key_check') is None
        plan=await repo.one(db,'EXPLAIN QUERY PLAN SELECT * FROM roulette_spins WHERE user_id=51 ORDER BY id DESC LIMIT 8')
        assert 'roulette_user_history' in plan['detail']
    result=await repo.spin(51,'migrated')
    assert result['transaction_id']==501 and result['balance']==62


@pytest.mark.asyncio
async def test_real_sqlite_lock_is_bounded_and_retry_is_safe(app):
    repo,_=repository(app)
    with sqlite3.connect(repo.db_path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(RouletteError,match='busy'):
            await repo.spin(51,'locked')
        blocker.rollback()
    assert (await repo.status(51))['spins']==0
    await repo.spin(51,'locked')
    assert (await repo.status(51))['spins']==1


@pytest.mark.asyncio
async def test_history_pages_and_disabled_prizes(app):
    repo,now=repository(app)
    config=await repo.config()
    prizes=[dict(id=row['id'],points=row['points'],weight=100 if row['points']==20 else 0,enabled=row['points']==20) for row in config['prizes']]
    await repo.save_config(99,1,prizes=prizes)
    for i in range(10):
        await repo.spin(51,str(i))
        now[0]+=timedelta(days=1)
    rows,page,pages=await repo.history(51,1)
    assert len(rows)==2 and page==1 and pages==2
    assert all(row['awarded_points']==20 for row in rows)
