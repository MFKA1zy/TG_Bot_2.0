import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio

from database.connection import init_db
from database.economy_repository import EconomyRepository,EconomyError
from database.repository import UserRepository
from services.catalog_service import CatalogService
from tests.test_catalog_phase2 import make_catalog,URL


@pytest_asyncio.fixture
async def app(tmp_path):
    catalog=await make_catalog(tmp_path)
    users=UserRepository(catalog.repository.db_path)
    for uid in (51,52,99):
        await users.ensure_user(uid,first_name=f"User {uid}",language="en")
        await users.set_captcha_passed(uid,True)
    await users.set_admin(99,True)
    for title in ("First","Second"):
        catalog.series.add_series(title,URL)
    return SimpleNamespace(catalog=catalog,users=users,economy=EconomyRepository(users.db_path))


@pytest.mark.asyncio
async def test_first_view_repeat_concurrency_history_and_restart(app):
    results=await asyncio.gather(*[app.catalog.confirm_view_reward(51,1) for _ in range(10)])
    assert sum(row.amount for row in results)==10
    assert sum(row.added for row in results)==1
    profile=await app.economy.profile(51)
    assert (profile["points_balance"],profile["points_earned"],profile["points_spent"],profile["watched"])==(10,10,0,1)
    history,_,_=await app.economy.history(51)
    assert len(history)==1 and history[0]["idempotency_key"]=="view:51:1"
    assert history[0]["series_title"]=="First"
    await init_db(app.economy.db_path)
    restarted=EconomyRepository(app.economy.db_path)
    assert (await restarted.confirm_view(51,1)).amount==0
    assert (await restarted.profile(51))["points_balance"]==10


@pytest.mark.asyncio
async def test_reward_configuration_zero_and_deleted_series_history(app):
    await app.economy.configure(99,"view_reward",25)
    assert (await app.catalog.confirm_view_reward(51,1)).amount==25
    await app.economy.configure(99,"view_reward",0)
    assert (await app.catalog.confirm_view_reward(51,2)).amount==0
    await app.economy.configure(99,"view_reward",100)
    assert (await app.catalog.confirm_view_reward(51,2)).amount==0
    app.catalog.series.delete_series(1)
    history,_,_=await app.economy.history(51)
    assert len(history)==2 and history[-1]["series_title"]=="First"
    assert (await app.economy.profile(51))["points_balance"]==25


@pytest.mark.asyncio
async def test_manual_credit_debit_idempotency_and_overspend(app):
    await asyncio.gather(*[app.economy.adjust(99,51,100,"Award","same-admin-request") for _ in range(6)])
    assert (await app.economy.profile(51))["points_balance"]==100
    await app.economy.adjust(99,51,-60,"Spent","debit")
    result=await app.economy.profile(51)
    assert (result["points_balance"],result["points_earned"],result["points_spent"])==(40,100,60)
    with pytest.raises(EconomyError,match="insufficient"):
        await app.economy.adjust(99,51,-41,"Overspend","overspend")
    assert (await app.economy.profile(51))["points_balance"]==40
    with pytest.raises(EconomyError,match="stale"):
        await app.economy.adjust(99,52,100,"Different target","same-admin-request")
    history,_,_=await app.economy.history(51)
    assert len(history)==2 and all(row["actor_id"]==99 for row in history)
    assert (await app.economy.statistics())["credited"]==100


@pytest.mark.asyncio
async def test_competing_debits_cannot_create_negative_balance(app):
    await app.economy.adjust(99,51,10,"Initial","initial")
    results=await asyncio.gather(app.economy.adjust(99,51,-8,"A","a"),app.economy.adjust(99,51,-8,"B","b"),return_exceptions=True)
    assert sum(isinstance(row,EconomyError) for row in results)==1
    assert (await app.economy.profile(51))["points_balance"]==2


@pytest.mark.asyncio
async def test_blocked_captcha_and_admin_permissions(app):
    with pytest.raises(EconomyError,match="access"):
        await app.economy.adjust(51,52,10,"Forged admin","bad")
    with pytest.raises(EconomyError,match="access"):
        await app.economy.configure(51,"view_reward",999)
    await app.users.set_user_blocked(51,True)
    with pytest.raises(EconomyError,match="access"):
        await app.catalog.confirm_view_reward(51,1)
    await app.users.set_user_blocked(51,False)
    await app.users.set_captcha_passed(51,False)
    with pytest.raises(EconomyError,match="access"):
        await app.catalog.confirm_view_reward(51,1)
    assert (await app.economy.history(51))[0]==[]


@pytest.mark.asyncio
async def test_profile_statistics_items_and_history_pagination(app):
    await app.catalog.confirm_view_reward(51,1)
    await app.catalog.set_favorite(51,1,True)
    await app.catalog.reviews.rating(51,1,9)
    row=await app.catalog.reviews.submit(51,1,"Published review for profile","published",[],"profile-review",0)
    profile=await app.economy.profile(51)
    assert (profile["watched"],profile["favorites"],profile["ratings"],profile["reviews"])==(1,1,1,1)
    for section in ("watched","favorites","ratings","reviews"):
        rows,page,pages=await app.economy.personal_items(51,section,999)
        assert len(rows)==1 and page==0 and pages==1
    for i in range(10):
        await app.economy.adjust(99,51,1,f"Award {i}",f"pagination:{i}")
    rows,page,pages=await app.economy.history(51,999)
    assert len(rows)==3 and page==1 and pages==2


@pytest.mark.asyncio
async def test_leaderboard_earned_balance_ties_bans_pages_indices(app):
    for user_id in range(1,26):
        await app.users.ensure_user(user_id,first_name=f"Person {user_id}",language="en")
        await app.economy.adjust(99,user_id,100,"Initial",f"rank:{user_id}")
    await app.economy.adjust(99,1,-99,"Spent","spent1")
    rows,page,pages,own,metric=await app.economy.leaderboard(1)
    assert rows[0]["first_name"]=="Person 1" and own["rank"]==1 and pages==3
    await app.economy.configure(99,"leaderboard","balance")
    rows,_,_,own,_=await app.economy.leaderboard(1)
    assert rows[0]["first_name"]=="Person 2" and own["rank"]==25
    await app.users.set_user_blocked(2,True)
    rows,_,_,_,_=await app.economy.leaderboard(1)
    assert rows[0]["first_name"]=="Person 3"
    assert (await app.economy.leaderboard(2))[3]["rank"] is None
    assert (await app.economy.leaderboard(1,999))[1]==2
    async with app.economy.connection() as db:
        plan=await app.economy.one(db,"EXPLAIN QUERY PLAN SELECT telegram_id FROM users WHERE is_blocked=0 ORDER BY points_balance DESC,telegram_id LIMIT 10")
        assert "leaderboard_balance" in plan["detail"]


@pytest.mark.asyncio
async def test_transaction_rolls_back_view_when_ledger_fails(app):
    async with app.economy.connection(True) as db:
        await db.execute("CREATE TRIGGER fail_points BEFORE INSERT ON points_ledger BEGIN SELECT RAISE(ABORT,'test rollback'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await app.catalog.confirm_view_reward(51,1)
    assert await app.catalog.repository.viewed_ids(51)==set()
    assert (await app.economy.profile(51))["points_balance"]==0


@pytest.mark.asyncio
async def test_legacy_migration_preview_confirmation_repeat_and_stale(tmp_path):
    path=tmp_path/"legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY,telegram_id INTEGER UNIQUE,username TEXT,first_name TEXT,last_name TEXT,
            is_admin INTEGER DEFAULT 0,captcha_passed INTEGER DEFAULT 1,language TEXT DEFAULT 'en',created_at TEXT,last_activity TEXT,is_blocked INTEGER DEFAULT 0,
            is_subscribed INTEGER DEFAULT 0,delivery_blocked INTEGER DEFAULT 0,subscription_checked_at TEXT);
            INSERT INTO users(telegram_id,is_admin) VALUES (51,0),(99,1);
            CREATE TABLE confirmed_views(user_id INTEGER,series_id INTEGER,confirmed_at TEXT,PRIMARY KEY(user_id,series_id));
            INSERT INTO confirmed_views VALUES (51,7,'2025-01-01T00:00:00+00:00');""")
    await init_db(path)
    economy=EconomyRepository(path)
    assert (await economy.confirm_view(51,7)).legacy is True
    assert (await economy.profile(51))["points_balance"]==0
    preview=await economy.legacy_preview(99)
    assert (preview["users"],preview["views"],preview["total"])==(1,1,10)
    assert (await economy.statistics())["operations"]==0
    await economy.configure(99,"view_reward",20)
    with pytest.raises(EconomyError,match="stale"):
        await economy.legacy_apply(99,preview)
    fresh=await economy.legacy_preview(99)
    assert (await economy.legacy_apply(99,fresh))["applied"]==1
    assert (await economy.legacy_apply(99,fresh))["applied"]==0
    assert (await economy.profile(51))["points_balance"]==20
    assert (await economy.confirm_view(51,7)).legacy is False
    await init_db(path)
    assert (await economy.legacy_preview(99))["views"]==0
    assert len(list(tmp_path.glob('legacy.db.pre-migration.*.bak')))==1


@pytest.mark.asyncio
async def test_legacy_concurrent_execution_and_audit_discrepancy(app):
    async with app.economy.connection(True) as db:
        await db.execute("INSERT INTO confirmed_views(user_id,series_id,confirmed_at) VALUES (51,1,'2025-01-01')")
    preview=await app.economy.legacy_preview(99)
    results=await asyncio.gather(*[app.economy.legacy_apply(99,preview) for _ in range(4)])
    assert sum(row["applied"] for row in results)==1
    assert (await app.economy.suspicious())[0]==[]
    async with app.economy.connection(True) as db:
        await db.execute("UPDATE users SET points_balance=99 WHERE telegram_id=51")
    assert (await app.economy.suspicious())[0][0]["telegram_id"]==51


@pytest.mark.asyncio
async def test_legacy_batch_rolls_back_all_rewards_on_failure(app):
    async with app.economy.connection(True) as db:
        await db.executemany("INSERT INTO confirmed_views(user_id,series_id,confirmed_at) VALUES (51,?,'2025-01-01')",[(1,),(2,)])
        await db.execute("CREATE TRIGGER fail_second_reward BEFORE INSERT ON points_ledger WHEN NEW.series_id=2 BEGIN SELECT RAISE(ABORT,'rollback legacy'); END")
    preview=await app.economy.legacy_preview(99)
    with pytest.raises(sqlite3.IntegrityError):
        await app.economy.legacy_apply(99,preview)
    assert (await app.economy.profile(51))["points_balance"]==0
    assert (await app.economy.statistics())["operations"]==0
    assert (await app.economy.legacy_preview(99))["views"]==2
