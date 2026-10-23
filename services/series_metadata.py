from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

RELEASE_STATUSES = ("ongoing", "completed", "announced")
PUBLICATION_STATUSES = ("published", "hidden")
FIELDS = {"alternative_titles", "description", "episode_count", "release_status",
          "category_ids", "episodes", "published_at", "publication_status", "recommended"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def validate_metadata(values: dict) -> dict:
    if values.keys() - FIELDS:
        raise ValueError("Unknown series field")
    result = dict(values)
    for name, maximum in (("description", 500),):
        if name in result and (not isinstance(result[name], str) or len(result[name]) > maximum):
            raise ValueError(name)
    if "alternative_titles" in result:
        titles = result["alternative_titles"]
        if not isinstance(titles, list) or len(titles) > 20 or any(not isinstance(t, str) or not t.strip() or len(t) > 256 for t in titles):
            raise ValueError("alternative_titles")
        result["alternative_titles"] = list(dict.fromkeys(t.strip() for t in titles))
    if "category_ids" in result:
        ids = result["category_ids"]
        if not isinstance(ids, list) or len(ids) > 20 or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError("category_ids")
        result["category_ids"] = list(dict.fromkeys(ids))
    if "episode_count" in result and (type(result["episode_count"]) is not int or not 0 <= result["episode_count"] <= 100000):
        raise ValueError("episode_count")
    if "release_status" in result and result["release_status"] not in RELEASE_STATUSES:
        raise ValueError("release_status")
    if "publication_status" in result and result["publication_status"] not in PUBLICATION_STATUSES:
        raise ValueError("publication_status")
    if "recommended" in result and type(result["recommended"]) is not bool:
        raise ValueError("recommended")
    if "published_at" in result:
        date = datetime.fromisoformat(result["published_at"])
        if date.tzinfo is None:
            raise ValueError("published_at requires a timezone")
        result["published_at"] = date.astimezone(timezone.utc).isoformat()
    if "episodes" in result:
        episodes = result["episodes"]
        if not isinstance(episodes, list) or len(episodes) > 100:
            raise ValueError("episodes")
        for episode in episodes:
            if not isinstance(episode, dict) or set(episode) != {"title", "url"}:
                raise ValueError("episodes")
            if not isinstance(episode["title"], str) or not 1 <= len(episode["title"]) <= 60:
                raise ValueError("episode title")
            validate_episode_url(episode["url"])
    return result


def validate_episode_url(url: str) -> None:
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
        raise ValueError("episode URL")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or "." not in host or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("episode URL")


def with_defaults(item: dict) -> dict:
    return {
        "alternative_titles": [], "description": "", "episode_count": 0,
        "release_status": "ongoing", "category_ids": [], "episodes": [],
        # A stable legacy date avoids changing the order on each read.
        "published_at": "1970-01-01T00:00:00+00:00",
        "updated_at": "1970-01-01T00:00:00+00:00",
        "publication_status": "published", "recommended": False, **item,
    }
