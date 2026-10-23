import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.access_service as access
from services.series_service import SeriesService, CatalogError
from tests.test_catalog_phase2 import URL


def test_catalog_cache_invalidates_across_instances_external_edits_and_corruption(tmp_path):
    first = SeriesService(tmp_path / "series.json")
    first.add_series("Original", URL)
    second = SeriesService(first.catalog_path)
    assert first.search_series("Original")
    second.edit_series(1, publication_status="hidden")
    assert first.search_series("Original") == []
    second.edit_series(1, publication_status="published", title="Changed")
    assert first.search_series("Original") == []
    assert first.search_series("Changed")[0]["id"] == 1
    result = first.get_series_by_id(1)
    result["category_ids"].append(999)
    assert first.get_series_by_id(1)["category_ids"] == []
    first.catalog_path.write_text('{"series": []}', encoding="utf-8")
    assert first.list_series() == []
    first.catalog_path.write_text('broken', encoding="utf-8")
    with pytest.raises(CatalogError):
        first.list_series()


@pytest.mark.asyncio
async def test_subscription_only_coalesces_inflight_and_never_caches_result(monkeypatch):
    monkeypatch.setattr(access, "settings", SimpleNamespace(SUBSCRIPTION_REQUIRED=True, SUBSCRIPTION_CHANNEL_ID="@channel"))
    status = "member"
    async def membership(*args):
        await asyncio.sleep(.01)
        return SimpleNamespace(status=status)
    bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=membership))
    assert all(await asyncio.gather(*[access.has_active_subscription(bot, 51) for _ in range(16)]))
    assert bot.get_chat_member.await_count == 1
    status = "left"
    assert await access.has_active_subscription(bot, 51) is False
    assert bot.get_chat_member.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_subscription_waiter_does_not_cancel_other_waiters(monkeypatch):
    monkeypatch.setattr(access, "settings", SimpleNamespace(SUBSCRIPTION_REQUIRED=True, SUBSCRIPTION_CHANNEL_ID="@channel"))
    entered = asyncio.Event()
    released = asyncio.Event()
    async def membership(*args):
        entered.set()
        await released.wait()
        return SimpleNamespace(status="member")
    bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=membership))
    first = asyncio.create_task(access.has_active_subscription(bot, 51))
    second = asyncio.create_task(access.has_active_subscription(bot, 51))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    released.set()
    assert await second is True
    assert bot.get_chat_member.await_count == 1
