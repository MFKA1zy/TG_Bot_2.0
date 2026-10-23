import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services.series_service import CatalogError, SeriesService


def test_series_service_search_exact_and_case_insensitive(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(
        json.dumps({
            "series": [
                {"id": 1, "title": "Загадочная соседка", "tiktok_url": "https://www.tiktok.com/@example/video/123"},
                {"id": 2, "title": "Последний эксперимент", "tiktok_url": "https://www.tiktok.com/@example/video/456"},
            ]
        }),
        encoding="utf-8",
    )

    service = SeriesService(catalog_path=catalog)

    assert service.search_series("Загадочная соседка")[0]["title"] == "Загадочная соседка"
    assert service.search_series("загадочная соседка")[0]["title"] == "Загадочная соседка"
    assert service.search_series("неизвестный") == []


def test_exact_match_takes_priority_and_partial_matches_are_supported(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(
        json.dumps(
            {
                "series": [
                    {"id": 1, "title": "Соседка", "tiktok_url": "https://www.tiktok.com/@a/video/1"},
                    {"id": 2, "title": "Загадочная соседка", "tiktok_url": "https://www.tiktok.com/@b/video/2"},
                    {"id": 3, "title": "Соседка 2", "tiktok_url": "https://www.tiktok.com/@c/video/3"},
                ]
            }
        ),
        encoding="utf-8",
    )
    service = SeriesService(catalog_path=catalog)

    exact = service.search_series("  СОСЕДКА ")
    assert [item["id"] for item in exact] == [1]
    assert [item["id"] for item in service.search_series("соседка 2")] == [3]
    assert [item["id"] for item in service.search_series("сосед")] == [1, 2, 3]


def test_search_finds_small_spelling_errors_and_ranks_closest_match_first(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(
        json.dumps(
            {
                "series": [
                    {
                        "id": 1,
                        "title": "Менталист",
                        "tiktok_url": "https://www.tiktok.com/@a/video/1",
                    },
                    {
                        "id": 2,
                        "title": "Последний эксперимент",
                        "tiktok_url": "https://www.tiktok.com/@b/video/2",
                    },
                    {
                        "id": 3,
                        "title": "Менталистка",
                        "tiktok_url": "https://www.tiktok.com/@c/video/3",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    service = SeriesService(catalog_path=catalog)

    assert [item["id"] for item in service.search_series("менталистт")] == [1, 3]
    assert service.search_series("xyz") == []


def test_series_service_add_and_delete_with_atomic_write(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(json.dumps({"series": []}), encoding="utf-8")

    service = SeriesService(catalog_path=catalog)
    item = service.add_series("Новый сериал", "https://www.tiktok.com/@example/video/999")

    assert item["id"] == 1
    assert item["title"] == "Новый сериал"
    assert len(service.list_series()) == 1

    service.delete_series(item["id"])
    assert service.list_series() == []


def test_series_service_rejects_invalid_tiktok_url(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(json.dumps({"series": []}), encoding="utf-8")

    service = SeriesService(catalog_path=catalog)

    with pytest.raises(ValueError):
        service.add_series("Bad", "https://example.com/not-tiktok")


def test_series_service_rejects_duplicate_and_edits_fields(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(json.dumps({"series": []}), encoding="utf-8")
    service = SeriesService(catalog_path=catalog)

    item = service.add_series("Test", "https://www.tiktok.com/@a/video/1")
    with pytest.raises(ValueError):
        service.add_series(" test ", "https://www.tiktok.com/@b/video/2")

    updated = service.edit_series(
        item["id"],
        title="Updated",
        tiktok_url="https://vm.tiktok.com/short",
    )
    assert updated["title"] == "Updated"
    assert updated["tiktok_url"] == "https://vm.tiktok.com/short"
    assert service.search_series("UPDAT")[0]["id"] == item["id"]
    assert service.delete_series(item["id"]) is True
    assert service.delete_series(item["id"]) is False


def test_series_photo_can_be_added_replaced_and_removed(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(json.dumps({"series": []}), encoding="utf-8")
    service = SeriesService(catalog_path=catalog)

    item = service.add_series(
        "Photo Series",
        "https://www.tiktok.com/@a/video/1",
        photo_file_id="telegram_photo_id_1",
        photo_url="https://i.ibb.co/abc123/poster.jpg",
    )
    assert item["photo_file_id"] == "telegram_photo_id_1"
    assert item["photo_url"] == "https://i.ibb.co/abc123/poster.jpg"
    assert service.get_series_by_id(item["id"])["photo_file_id"] == "telegram_photo_id_1"

    replaced = service.set_series_photo(
        item["id"],
        "telegram_photo_id_2",
        "https://i.ibb.co/def456/new-poster.jpg",
    )
    assert replaced["photo_file_id"] == "telegram_photo_id_2"
    assert replaced["photo_url"] == "https://i.ibb.co/def456/new-poster.jpg"

    edited = service.edit_series(item["id"], title="Renamed Photo Series")
    assert edited["photo_file_id"] == "telegram_photo_id_2"

    removed = service.set_series_photo(item["id"], None)
    assert "photo_file_id" not in removed
    assert "photo_url" not in removed
    assert "photo_file_id" not in service.get_series_by_id(item["id"])
    assert service.delete_series(item["id"]) is True
    assert service.list_series() == []


def test_series_photo_file_id_validation(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(json.dumps({"series": []}), encoding="utf-8")
    service = SeriesService(catalog_path=catalog)

    with pytest.raises(ValueError):
        service.add_series(
            "Photo Series",
            "https://www.tiktok.com/@a/video/1",
            photo_file_id="invalid file id",
        )


def test_series_service_rejects_non_imgbb_photo_urls(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(json.dumps({"series": []}), encoding="utf-8")
    service = SeriesService(catalog_path=catalog)

    with pytest.raises(ValueError):
        service.add_series(
            "Photo Series",
            "https://www.tiktok.com/@a/video/1",
            photo_url="https://example.com/poster.jpg",
        )


def test_corrupt_catalog_is_backed_up_and_not_replaced(tmp_path):
    catalog = tmp_path / "series.json"
    original = "{invalid json"
    catalog.write_text(original, encoding="utf-8")
    service = SeriesService(catalog_path=catalog)

    with pytest.raises(CatalogError):
        service.list_series()

    assert catalog.read_text(encoding="utf-8") == original
    backups = list(tmp_path.glob("series.json.corrupt.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_concurrent_additions_do_not_overwrite_each_other(tmp_path):
    catalog = tmp_path / "series.json"
    catalog.write_text(json.dumps({"series": []}), encoding="utf-8")
    service = SeriesService(catalog_path=catalog)

    def add(index: int) -> dict:
        return service.add_series(
            f"Series {index}",
            f"https://www.tiktok.com/@example/video/{index + 1}",
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(add, range(5)))

    items = service.list_series()
    assert len(items) == 5
    assert {item["id"] for item in items} == {1, 2, 3, 4, 5}
