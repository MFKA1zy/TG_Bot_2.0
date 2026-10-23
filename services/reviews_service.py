from __future__ import annotations

import asyncio

from database.reviews_repository import ReviewsRepository, ReviewError
from services.catalog_service import CatalogService
from services.moderation_service import moderate_text, normalize


class ReviewsService:
    def __init__(self, catalog=None, repository=None):
        self.catalog = catalog or CatalogService()
        self.repository = repository or ReviewsRepository(self.catalog.repository.db_path)

    async def require_series(self, series_id: int) -> dict:
        item = await asyncio.to_thread(self.catalog.published, series_id)
        if not item:
            raise ReviewError("missing")
        return item

    async def rate(self, user_id: int, series_id: int, score: int, request_key: str | None = None):
        await self.require_series(series_id)
        await self.repository.rating(user_id, series_id, score, request_key)

    async def submit(self, user_id: int, series_id: int, text: str, request_key: str) -> dict:
        await self.require_series(series_id)
        text = text.strip()
        if not 10 <= len(text) <= 1000:
            raise ReviewError("length")
        policy, words = await asyncio.gather(self.repository.policy(), self.repository.words())
        result = await asyncio.to_thread(moderate_text, text, words)
        status = "published" if result.level == "safe" and policy["auto_safe"] else "pending"
        if result.level == "forbidden":
            status = policy["forbidden_action"]
        return await self.repository.submit(user_id, series_id, text, status, result.reasons, request_key, policy["review_cooldown"])

    async def report(self, user_id: int, review_id: int, reason: str):
        row = await self.repository.review(review_id)
        if not row:
            raise ReviewError("missing")
        await self.require_series(row["series_id"])
        await self.repository.report(user_id, review_id, reason)

    async def admin_action(self, actor: int, action: str, target: int, reason: str, request_key: str, version=0, extra=0):
        if action == "published":
            row = await self.repository.review(target)
            if not row:
                raise ReviewError("missing")
            await self.require_series(row["series_id"])
        await self.repository.admin_action(actor, action, target, reason, request_key, version, extra)
