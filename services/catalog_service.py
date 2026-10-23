from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from database.catalog_repository import CatalogRepository
from services.series_service import SeriesService

SECTIONS = ("all", "categories", "new", "trending", "popular", "recommended", "favorites")


@dataclass
class CatalogPage:
    items: list[dict]
    page: int
    pages: int
    total: int


class CatalogService:
    def __init__(self, series: SeriesService | None = None, repository: CatalogRepository | None = None):
        self.series = series or SeriesService()
        self.repository = repository or CatalogRepository()

    def published(self, series_id: int) -> dict | None:
        item = self.series.get_series_by_id(series_id)
        return item if item and item["publication_status"] == "published" else None

    async def page(self, section: str, user_id: int, page: int = 0, category_id: int = 0, page_size: int = 8) -> CatalogPage:
        if section not in SECTIONS or not 1 <= page_size <= 50:
            raise ValueError("Invalid catalog section")
        items = [item for item in self.series.list_series() if item["publication_status"] == "published"]
        if section == "categories":
            valid_ids = {row["id"] for row in await self.repository.categories()}
            items = [item for item in items if category_id in valid_ids and category_id in item["category_ids"]]
        elif section == "recommended":
            items = [item for item in items if item["recommended"]]
        elif section == "favorites":
            favorites = await self.repository.favorite_ids(user_id)
            items = [item for item in items if item["id"] in favorites]
        elif section in ("trending", "popular"):
            counts = await self.repository.view_counts()
            metric = 1 if section == "trending" else 0
            items = [item for item in items if counts.get(item["id"], (0, 0))[metric] > 0]
            items.sort(key=lambda item: (-counts[item["id"]][metric], item["id"]))
        if section == "new":
            items.sort(key=lambda item: (datetime.fromisoformat(item["published_at"]), item["id"]), reverse=True)
        elif section not in ("trending", "popular"):
            items.sort(key=lambda item: item["id"])
        total = len(items)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(0, page), pages - 1)
        return CatalogPage(items[page * page_size:(page + 1) * page_size], page, pages, total)

    async def set_favorite(self, user_id: int, series_id: int, enabled: bool) -> None:
        if not self.published(series_id):
            raise ValueError("Series unavailable")
        await self.repository.set_favorite(user_id, series_id, enabled)

    async def confirm_view(self, user_id: int, series_id: int) -> bool:
        if not self.published(series_id):
            raise ValueError("Series unavailable")
        return await self.repository.confirm_view(user_id, series_id)

    async def edit_metadata(self, series_id: int, **values) -> dict:
        if "category_ids" in values:
            existing = {row["id"] for row in await self.repository.categories()}
            if set(values["category_ids"]) - existing:
                raise ValueError("Category not found")
        return self.series.edit_series(series_id, **values)
