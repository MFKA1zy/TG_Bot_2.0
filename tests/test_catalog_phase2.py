import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from database.catalog_repository import CatalogRepository
from database.connection import init_db
from database.repository import UserRepository
from services.catalog_service import CatalogService
from services.series_metadata import validate_metadata
from services.series_service import SeriesService, CatalogError

URL = "https://www.tiktok.com/@test/video/123"


async def make_catalog(tmp_path):
    db = tmp_path / "users.db"
    await init_db(db)
    return CatalogService(SeriesService(tmp_path / "series.json"), CatalogRepository(db))


def test_legacy_json_migration_preserves_ids_posters_metadata_and_is_idempotent(tmp_path):
    path = tmp_path / "series.json"
    original = {"custom": {"preserve": True}, "series": [{"id": 8, "title": "Legacy", "tiktok_url": URL, "photo_file_id": "old-photo", "photo_url": "https://i.ibb.co/abc/poster.jpg"}]}
    path.write_text(json.dumps(original), encoding="utf-8")
    service = SeriesService(path)
    service.migrate_catalog()
    payload = json.loads(path.read_text())
    assert payload["custom"] == original["custom"]
    assert payload["next_id"] == 9
    item = service.get_series_by_id(8)
    for key, value in original["series"][0].items():
        assert item[key] == value
    assert item["publication_status"] == "published"
    backup = list(tmp_path.glob("*.pre-v2.*.bak"))
    assert len(backup) == 1
    assert json.loads(backup[0].read_text()) == original
    service.migrate_catalog()
    assert len(list(tmp_path.glob("*.pre-v2.*.bak"))) == 1


def test_ids_never_reused_and_top_level_metadata_survives_all_mutations(tmp_path):
    path = tmp_path / "series.json"
    path.write_text(json.dumps({"custom": "kept", "series": []}))
    service = SeriesService(path)
    first = service.add_series("First", URL)
    second = service.add_series("Second", URL)
    service.edit_series(second["id"], description="Description")
    service.set_series_photo(second["id"], "photo-id", "https://i.ibb.co/a/photo.jpg")
    service.delete_series(second["id"])
    third = SeriesService(path).add_series("Third", URL)
    assert third["id"] > second["id"] > first["id"]
    assert json.loads(path.read_text())["custom"] == "kept"


@pytest.mark.asyncio
async def test_categories_crud_order_casefold_uniqueness_and_restart(tmp_path):
    catalog = await make_catalog(tmp_path)
    repo = catalog.repository
    a = await repo.save_category("Drama", 20)
    b = await repo.save_category("Comedy", 10)
    assert [row["id"] for row in await repo.categories()] == [b, a]
    with pytest.raises(ValueError):
        await repo.save_category("DRAMA")
    await repo.save_category("New name", -1, a)
    restarted = CatalogRepository(repo.db_path)
    assert [row["id"] for row in await restarted.categories()] == [a, b]
    await repo.delete_category(a)
    c = await repo.save_category("Next")
    assert c > b


@pytest.mark.asyncio
async def test_edit_every_metadata_field_and_validate_category_references(tmp_path):
    catalog = await make_catalog(tmp_path)
    category = await catalog.repository.save_category("Genre")
    item = catalog.series.add_series("Series", URL)
    values = {"alternative_titles": ["Alias", "Second name"], "description": "Plot", "episode_count": 12,
              "release_status": "completed", "category_ids": [category], "episodes": [{"title": "Episode 1", "url": URL}],
              "published_at": "2026-10-23T15:00:00+06:00", "publication_status": "hidden", "recommended": True}
    updated = await catalog.edit_metadata(item["id"], **values)
    assert updated["published_at"] == "2026-10-23T09:00:00+00:00"
    for key in values.keys() - {"published_at"}:
        assert updated[key] == values[key]
    assert updated["updated_at"] > item["updated_at"]
    with pytest.raises(ValueError):
        await catalog.edit_metadata(item["id"], category_ids=[999])
    assert catalog.published(item["id"]) is None
    assert catalog.series.search_series("Alias") == []
    await catalog.edit_metadata(item["id"], publication_status="published")
    assert catalog.series.search_series("Alias")[0]["id"] == item["id"]


@pytest.mark.parametrize("values", [
    {"episode_count": -1}, {"episode_count": True}, {"description": "x" * 501},
    {"category_ids": [-1]}, {"category_ids": [True]}, {"recommended": 1},
    {"release_status": "bad"}, {"publication_status": "bad"},
    {"published_at": "2026-01-01"}, {"alternative_titles": [""]},
    {"episodes": [{"title": "Ep", "url": "javascript:alert(1)"}]},
    {"episodes": [{"title": "Ep", "url": "https://user:secret@example.com/a"}]},
    {"episodes": [{"title": "Ep", "url": "https://example.com:999/a"}]},
])
def test_invalid_metadata_is_rejected(values):
    with pytest.raises((ValueError, TypeError)):
        validate_metadata(values)


@pytest.mark.asyncio
async def test_public_pages_visibility_categories_new_recommended_favorites(tmp_path):
    catalog = await make_catalog(tmp_path)
    category = await catalog.repository.save_category("Genre")
    for number in range(11):
        catalog.series.add_series(f"Series {number}", URL, category_ids=[category], published_at=f"2026-10-{number + 1:02d}T00:00:00+00:00", recommended=number == 5)
    catalog.series.add_series("Hidden", URL, publication_status="hidden")
    assert (await catalog.page("all", 51)).total == 11
    assert len((await catalog.page("all", 51)).items) == 8
    last = await catalog.page("all", 51, page=999)
    assert last.page == 1 and len(last.items) == 3
    assert (await catalog.page("all", 51, page=-3)).page == 0
    assert (await catalog.page("new", 51)).items[0]["title"] == "Series 10"
    assert (await catalog.page("recommended", 51)).items[0]["title"] == "Series 5"
    assert (await catalog.page("categories", 51, category_id=category)).total == 11
    assert (await catalog.page("categories", 51, category_id=999)).total == 0
    await catalog.set_favorite(51, 1, True)
    await catalog.set_favorite(51, 1, True)
    assert (await catalog.page("favorites", 51)).total == 1
    assert (await catalog.page("favorites", 52)).total == 0
    await catalog.set_favorite(51, 1, False)
    assert (await catalog.page("favorites", 51)).items == []
    with pytest.raises(ValueError):
        await catalog.set_favorite(51, 12, True)
    await catalog.repository.delete_category(category)
    assert (await catalog.page("categories", 51, category_id=category)).items == []


@pytest.mark.asyncio
async def test_views_unique_concurrent_persistent_and_trend_period(tmp_path):
    catalog = await make_catalog(tmp_path)
    first = catalog.series.add_series("First", URL)["id"]
    second = catalog.series.add_series("Second", URL)["id"]
    results = await asyncio.gather(*[catalog.confirm_view(51, first) for _ in range(8)])
    assert results.count(True) == 1
    await catalog.confirm_view(52, first)
    await catalog.confirm_view(51, second)
    with sqlite3.connect(catalog.repository.db_path) as db:
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        db.execute("UPDATE confirmed_views SET confirmed_at=? WHERE series_id=?", (old, first))
    assert [i["id"] for i in (await catalog.page("popular", 51)).items] == [first, second]
    assert [i["id"] for i in (await catalog.page("trending", 51)).items] == [second]
    await catalog.repository.set_trend_days(14)
    assert [i["id"] for i in (await catalog.page("trending", 51)).items] == [first, second]
    await init_db(catalog.repository.db_path)
    restarted = CatalogService(SeriesService(catalog.series.catalog_path), CatalogRepository(catalog.repository.db_path))
    assert await restarted.confirm_view(51, first) is False
    assert await restarted.repository.trend_days() == 14
    assert await restarted.repository.viewed_ids(51) == {first, second}
    for value in (0, 366, -1):
        with pytest.raises(ValueError):
            await restarted.repository.set_trend_days(value)


@pytest.mark.asyncio
async def test_empty_catalog_and_schema_restart(tmp_path):
    catalog = await make_catalog(tmp_path)
    for section in ("all", "categories", "new", "popular", "trending", "favorites", "recommended"):
        page = await catalog.page(section, 51)
        assert page.items == [] and page.pages == 1 and page.page == 0
    await init_db(catalog.repository.db_path)
    assert await catalog.repository.categories() == []


def test_malformed_metadata_never_replaces_catalog(tmp_path):
    path = tmp_path / "series.json"
    source = json.dumps({"series": [{"id": 1, "title": "Bad", "tiktok_url": URL, "episodes": "broken"}]})
    path.write_text(source)
    service = SeriesService(path)
    with pytest.raises(CatalogError):
        service.migrate_catalog()
    assert path.read_text() == source
    assert len(list(tmp_path.glob("*.corrupt.*.bak"))) == 1


@pytest.mark.asyncio
async def test_transport_block_is_distinct_from_admin_ban(tmp_path):
    catalog = await make_catalog(tmp_path)
    users = UserRepository(catalog.repository.db_path)
    await users.ensure_user(51)
    await users.set_delivery_blocked(51, True)
    assert await users.get_active_user_ids() == []
    assert (await users.get_user_by_telegram_id(51)).is_blocked is False
    await users.ensure_user(51)
    assert await users.get_active_user_ids() == [51]
    await users.set_user_blocked(51, True)
    await users.ensure_user(51)
    assert (await users.get_user_by_telegram_id(51)).is_blocked is True
    assert await users.get_active_user_ids() == []


def test_unknown_schema_is_not_overwritten_and_publication_sets_date(tmp_path):
    path = tmp_path / "series.json"
    path.write_text('{"schema_version": 99, "series": []}')
    service = SeriesService(path)
    with pytest.raises(CatalogError):
        service.migrate_catalog()
    assert json.loads(path.read_text())["schema_version"] == 99
    path.write_text('{"series": []}')
    item = service.add_series("Draft", URL, publication_status="hidden", published_at="2000-01-01T00:00:00+00:00")
    published = service.edit_series(item["id"], publication_status="published")
    assert datetime.fromisoformat(published["published_at"]).year > 2000
