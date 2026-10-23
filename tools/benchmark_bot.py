"""Reproducible local timings. No live Telegram requests or production data."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.connection import init_db
from database.catalog_repository import CatalogRepository
from services.catalog_service import CatalogService
from services.series_service import SeriesService
from services.series_cards import render_card
from services import access_service
from types import SimpleNamespace
from datetime import datetime, timezone
from database.economy_repository import EconomyRepository
from database.roulette_repository import RouletteRepository
from database.reviews_repository import ReviewsRepository


async def measure(action, repeats=40):
    values = []
    for _ in range(repeats):
        start = time.perf_counter()
        await action()
        values.append((time.perf_counter() - start) * 1000)
    return {"median_ms": round(statistics.median(values), 3), "p95_ms": round(sorted(values)[int(len(values) * .95) - 1], 3)}


async def run():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "series.json"
        path.write_text(json.dumps({"series": [{"id": i, "title": f"Series {i}", "tiktok_url": f"https://www.tiktok.com/@test/video/{i}", "description": "Plot " * 40} for i in range(1, 1001)]}), encoding="utf-8")
        db = root / "users.db"
        await init_db(db)
        series = SeriesService(path)
        repo = CatalogRepository(db)
        catalog = CatalogService(series, repo)
        economy = EconomyRepository(db)
        roulette = RouletteRepository(db,randbelow=lambda n:75)
        reviews = ReviewsRepository(db)
        timestamp=datetime.now(timezone.utc).isoformat()
        async with economy.connection(True) as connection:
            await connection.executemany("INSERT INTO users(telegram_id,first_name,language,captcha_passed) VALUES (?,?,'en',1)",[(uid,f'User {uid}') for uid in range(1,1201)])
            await connection.executemany("INSERT INTO ratings VALUES (?,?,8,?)",[(uid,uid%1000+1,timestamp) for uid in range(1,1201)])
            await connection.executemany("INSERT INTO reviews(user_id,series_id,text,status,created_at,updated_at,published_at) VALUES (?,?,'Detailed synthetic review','published',?,?,?)",[(uid,uid%1000+1,timestamp,timestamp,timestamp) for uid in range(1,1201)])
            await connection.executemany("INSERT INTO confirmed_views(user_id,series_id,confirmed_at,reward_eligible) VALUES (?,?,?,1)",[(uid,series_id,timestamp) for uid in range(1,1201) for series_id in range(1,11)])
        async def listing():
            await asyncio.to_thread(series.list_series)
        async def inline():
            await asyncio.to_thread(series.search_series, "Series 42")
        async def card():
            item = await asyncio.to_thread(series.get_series_by_id, 42)
            if hasattr(repo, "card_state"):
                state, categories = await asyncio.gather(repo.card_state(51, 42), repo.categories())
                item.update(state)
                render_card(item, categories, "en", state["views"])
            else:
                await repo.favorite_ids(51)
                await repo.viewed_ids(51)
                counts = await repo.view_counts()
                categories = await repo.categories()
                render_card(item, categories, "en", counts.get(42, (0, 0))[0])
        async def page():
            await catalog.page("new", 51)
        async def sqlite_counts():
            await repo.view_counts()
        membership_requests = 0
        async def membership(*args):
            nonlocal membership_requests
            membership_requests += 1
            await asyncio.sleep(.01)  # synthetic RTT, explicitly not a live API measurement
            return SimpleNamespace(status="member")
        original_settings = access_service.settings
        access_service.settings = SimpleNamespace(SUBSCRIPTION_REQUIRED=True, SUBSCRIPTION_CHANNEL_ID="@test")
        bot = SimpleNamespace(get_chat_member=membership)
        started = time.perf_counter()
        try:
            await asyncio.gather(*[access_service.has_active_subscription(bot, 51) for _ in range(16)])
        finally:
            access_service.settings = original_settings
        subscription = {"synthetic_rtt_ms": 10, "simultaneous_checks": 16, "api_requests": membership_requests,
                        "batch_ms": round((time.perf_counter() - started) * 1000, 3)}
        # Execute the actual callback handler, with a deterministic Telegram stub.
        from aiogram import Bot, Dispatcher, Router, F
        from aiogram.client.session.base import BaseSession
        from aiogram.types import Update, Message, Chat
        from database.repository import UserRepository
        import handlers.catalog as ui
        from services.message_service import message_service

        class Session(BaseSession):
            def __init__(self):
                super().__init__()
                self.started = 0
                self.ack = []
            async def close(self):
                pass
            async def stream_content(self, url, **kwargs):
                if False:
                    yield b""
            async def make_request(self, bot, method, timeout=None):
                if method.__api_method__ == "answerCallbackQuery":
                    self.ack.append((time.perf_counter() - self.started) * 1000)
                    return True
                return Message(message_id=10, date=datetime.now(timezone.utc), chat=Chat(id=51, type="private"), text=getattr(method, "text", ""))

        users = UserRepository(str(db))
        await users.ensure_user(51, language="en")
        await users.set_captcha_passed(51, True)
        session = Session()
        bot = Bot("12345:BENCH", session=session)
        async def subscription_stub(*args):
            await asyncio.sleep(.01)
            return True
        ui.catalog, ui.users, ui.has_active_subscription = catalog, users, subscription_stub
        original_tracking = message_service.track_message
        message_service.track_message = lambda *args: None
        dp = Dispatcher()
        router = Router()
        router.callback_query.register(ui.catalog_callback, F.data.startswith("cat:"))
        dp.include_router(router)
        sequence = 0
        async def click():
            nonlocal sequence
            sequence += 1
            session.started = time.perf_counter()
            update = Update.model_validate({"update_id": sequence, "callback_query": {
                "id": str(sequence), "chat_instance": "bench", "from": {"id": 51, "is_bot": False, "first_name": "Test"},
                "data": "cat:card:42:all:0:0", "message": {"message_id": 10, "date": 1, "chat": {"id": 51, "type": "private"}, "text": "old"}}})
            await dp.feed_update(bot, update)
        try:
            callbacks = await measure(click, 20)
            callbacks["ack_median_ms"] = round(statistics.median(session.ack), 3)
            callbacks["subscription_synthetic_rtt_ms"] = 10
        finally:
            message_service.track_message = original_tracking
            await dp.storage.close()
            await bot.session.close()
        async def parallel_search():
            await asyncio.gather(*[asyncio.to_thread(series.search_series,f'Series {i}') for i in range(1,33)])
        spin_users=iter(range(100,140))
        async def new_spin():
            uid=next(spin_users)
            await roulette.spin(uid,f'benchmark:{uid}')
        return {"environment": "local Windows/Python; 1000 series; 1200 users; 12000 views; temporary SQLite; no Telegram network", "repeats": 40,
                "catalog_read": await measure(listing), "inline_exact_search": await measure(inline),
                "card_storage_render": await measure(card), "new_page": await measure(page),
                "sqlite_view_counts": await measure(sqlite_counts), "subscription_synthetic": subscription,
                "callback_dispatch_synthetic": callbacks,
                "ratings":await measure(lambda:reviews.rating_summary(42,51)),
                "public_reviews":await measure(lambda:reviews.public_reviews(42,0)),
                "profile":await measure(lambda:economy.profile(51)),
                "leaderboard":await measure(lambda:economy.leaderboard(51)),
                "roulette_status":await measure(lambda:roulette.status(51)),
                "roulette_atomic_spin":await measure(new_spin),
                "parallel_search_batch_32":await measure(parallel_search,10)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run())
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
