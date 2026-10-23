from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

from config import settings
from services.series_metadata import FIELDS, utc_now, validate_metadata, with_defaults
from utils.text import normalize_query, normalize_text

_CATALOG_LOCK = threading.RLock()
MAX_TITLE_LENGTH = 256
MAX_URL_LENGTH = 2048


class CatalogError(RuntimeError):
    """Raised when the series catalog cannot safely be read or written."""


class SeriesService:
    def __init__(self, catalog_path: str | Path | None = None) -> None:
        self.catalog_path = (
            Path(catalog_path)
            if catalog_path
            else settings.SERIES_PATH
        )
        self._lock = _CATALOG_LOCK
        self._ensure_catalog()

    def _ensure_catalog(self) -> None:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.catalog_path.exists():
            self._write_atomic({"series": []})

    def _backup_corrupt_catalog(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.catalog_path.with_name(
            f"{self.catalog_path.name}.corrupt.{timestamp}.bak"
        )
        suffix = 1
        while backup_path.exists():
            backup_path = self.catalog_path.with_name(
                f"{self.catalog_path.name}.corrupt.{timestamp}.{suffix}.bak"
            )
            suffix += 1
        try:
            shutil.copy2(self.catalog_path, backup_path)
        except OSError as exc:
            raise CatalogError(
                "Каталог повреждён, но резервную копию создать не удалось."
            ) from exc
        return backup_path

    def _load_catalog(self) -> dict:
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            backup_path = self._backup_corrupt_catalog()
            raise CatalogError(
                f"Файл каталога повреждён; резервная копия: {backup_path.name}"
            ) from exc
        except OSError as exc:
            raise CatalogError("Не удалось прочитать каталог сериалов.") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
            backup_path = self._backup_corrupt_catalog()
            raise CatalogError(
                f"Структура каталога повреждена; резервная копия: {backup_path.name}"
            )
        items = payload["series"]
        version = payload.get("schema_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise CatalogError("Unsupported catalog schema version")
        if any(not isinstance(item, dict) for item in items):
            backup_path = self._backup_corrupt_catalog()
            raise CatalogError(
                f"Записи каталога повреждены; резервная копия: {backup_path.name}"
            )
        seen_ids: set[int] = set()
        for item in items:
            try:
                item_id = int(item["id"])
                title = normalize_text(item["title"])
                url = str(item["tiktok_url"])
                self.validate_tiktok_url(url)
            except (KeyError, TypeError, ValueError):
                backup_path = self._backup_corrupt_catalog()
                raise CatalogError(
                    f"Запись каталога повреждена; резервная копия: {backup_path.name}"
                )
            if (
                item_id <= 0
                or item_id in seen_ids
                or not title
                or len(title) > MAX_TITLE_LENGTH
            ):
                backup_path = self._backup_corrupt_catalog()
                raise CatalogError(
                    f"Запись каталога повреждена; резервная копия: {backup_path.name}"
                )
            photo_file_id = item.get("photo_file_id")
            if photo_file_id is not None and (
                not isinstance(photo_file_id, str)
                or not photo_file_id.strip()
                or len(photo_file_id) > 2048
            ):
                backup_path = self._backup_corrupt_catalog()
                raise CatalogError(
                    f"Фото в записи каталога повреждено; резервная копия: {backup_path.name}"
                )
            photo_url = item.get("photo_url")
            if photo_url is not None:
                try:
                    self.validate_imgbb_url(photo_url)
                except ValueError:
                    backup_path = self._backup_corrupt_catalog()
                    raise CatalogError(
                        f"Ссылка на фото повреждена; резервная копия: {backup_path.name}"
                    )
            seen_ids.add(item_id)
        for item in items:
            try:
                validate_metadata({key: item[key] for key in FIELDS if key in item})
            except (ValueError, TypeError):
                self._backup_corrupt_catalog()
                raise CatalogError("Invalid series metadata")
        highest = max(seen_ids, default=0)
        next_id = payload.get("next_id", highest + 1)
        if type(next_id) is not int or next_id <= highest:
            self._backup_corrupt_catalog()
            raise CatalogError("Invalid catalog next_id")
        return {**payload, "series": [with_defaults({**item, "id": int(item["id"])}) for item in items], "next_id": next_id}

    def _write_atomic(self, payload: dict) -> None:
        temp_path = self.catalog_path.with_name(
            f".{self.catalog_path.name}.{os.getpid()}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.catalog_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise CatalogError("Не удалось сохранить каталог сериалов.") from exc

    def list_series(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._load_catalog()["series"])

    def migrate_catalog(self) -> None:
        with self._lock:
            payload = self._load_catalog()
            if payload.get("schema_version", 1) >= 2:
                return
            backup = self.catalog_path.with_name(self.catalog_path.name + ".pre-v2." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
            shutil.copy2(self.catalog_path, backup)
            self._write_atomic({**payload, "schema_version": 2})

    def get_series_by_id(self, series_id: int) -> dict | None:
        for item in self.list_series():
            try:
                if int(item.get("id", -1)) == int(series_id):
                    return item
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def validate_tiktok_url(url: str) -> None:
        value = (url or "").strip()
        parsed = urlparse(value)
        allowed_hosts = {
            "tiktok.com",
            "www.tiktok.com",
            "m.tiktok.com",
            "vm.tiktok.com",
        }
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.hostname.lower() not in allowed_hosts
            or not parsed.path.strip("/")
            or len(value) > MAX_URL_LENGTH
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 80, 443)
        ):
            raise ValueError("Укажите корректную ссылку на TikTok.")

    @staticmethod
    def validate_imgbb_url(url: str) -> None:
        if not isinstance(url, str) or len(url) > MAX_URL_LENGTH:
            raise ValueError("Укажите корректную прямую ссылку на изображение.")
        parsed = urlparse(url.strip())
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or hostname != "i.ibb.co"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or not parsed.path.strip("/")
        ):
            raise ValueError("ImgBB вернул некорректную ссылку на изображение.")

    @staticmethod
    def _normalize_title(title: str) -> str:
        return normalize_query(title)

    def search_series(self, query: str) -> list[dict]:
        normalized_query = self._normalize_title(query)
        if not normalized_query:
            return []

        matches: list[tuple[int, dict]] = []
        items = [item for item in self.list_series() if item.get("publication_status", "published") == "published"]
        for item in items:
            names = [self._normalize_title(str(name)) for name in [item["title"], *item.get("alternative_titles", [])]]
            if normalized_query in names:
                matches.append((0, item))
            elif any(normalized_query in name for name in names):
                matches.append((1, item))
        matches.sort(key=lambda pair: (pair[0], int(pair[1].get("id", 0))))
        if matches:
            exact_matches = [item for priority, item in matches if priority == 0]
            if exact_matches:
                return exact_matches
            return [item for _, item in matches]
        if len(normalized_query.replace(" ", "")) < 4:
            return []

        query_compact = normalized_query.replace(" ", "")
        query_words = normalized_query.split()
        minimum_score = 0.74 if len(query_compact) <= 5 else 0.70
        fuzzy_matches: list[tuple[float, int, dict]] = []
        for item in items:
            title_norm = self._normalize_title(str(item.get("title", "")))
            title_words = title_norm.split()
            title_compact = title_norm.replace(" ", "")
            scores = [
                SequenceMatcher(None, query_compact, title_compact).ratio(),
                SequenceMatcher(
                    None,
                    " ".join(sorted(query_words)),
                    " ".join(sorted(title_words)),
                ).ratio(),
            ]
            if len(query_words) == 1:
                scores.extend(
                    SequenceMatcher(None, query_words[0], title_word).ratio()
                    for title_word in title_words
                )
            else:
                word_scores = [
                    max(
                        (
                            SequenceMatcher(None, query_word, title_word).ratio()
                            for title_word in title_words
                        ),
                        default=0.0,
                    )
                    for query_word in query_words
                ]
                scores.append(sum(word_scores) / len(word_scores))
            score = max(scores)
            if score >= minimum_score:
                fuzzy_matches.append((score, int(item["id"]), item))

        fuzzy_matches.sort(key=lambda match: (-match[0], match[1]))
        return [item for _, _, item in fuzzy_matches]

    def add_series(
        self,
        title: str,
        tiktok_url: str,
        *,
        photo_file_id: str | None = None,
        photo_url: str | None = None,
        **metadata,
    ) -> dict:
        metadata = validate_metadata(metadata)
        clean_title = normalize_text(title)
        if not clean_title:
            raise ValueError("Название сериала не может быть пустым.")
        if len(clean_title) > MAX_TITLE_LENGTH:
            raise ValueError("Название не должно быть длиннее 256 символов.")
        self.validate_tiktok_url(tiktok_url)
        if photo_file_id is not None:
            self._validate_photo_file_id(photo_file_id)
        if photo_url is not None:
            self.validate_imgbb_url(photo_url)

        with self._lock:
            payload = self._load_catalog()
            items = payload["series"]
            normalized_title = self._normalize_title(clean_title)
            if any(
                self._normalize_title(str(item.get("title", ""))) == normalized_title
                for item in items
            ):
                raise ValueError("Такой сериал уже существует.")
            next_id = payload["next_id"]
            new_item = with_defaults({
                "id": next_id,
                "title": clean_title,
                "tiktok_url": tiktok_url.strip(),
                "published_at": utc_now(), "updated_at": utc_now(), **metadata,
            })
            if photo_file_id:
                new_item["photo_file_id"] = photo_file_id
            if photo_url:
                new_item["photo_url"] = photo_url
            self._write_atomic({**payload, "schema_version": 2, "next_id": next_id + 1, "series": [*items, new_item]})
            return dict(new_item)

    def edit_series(
        self,
        series_id: int,
        *,
        title: str | None = None,
        tiktok_url: str | None = None,
        **metadata,
    ) -> dict:
        metadata = validate_metadata(metadata)
        with self._lock:
            payload = self._load_catalog()
            items = payload["series"]
            selected = None
            for item in items:
                try:
                    if int(item.get("id", -1)) == int(series_id):
                        selected = item
                        break
                except (TypeError, ValueError):
                    continue
            if selected is None:
                raise ValueError("Сериал не найден.")

            updated = {**selected, "updated_at": utc_now()}
            if title is not None:
                clean_title = normalize_text(title)
                if not clean_title:
                    raise ValueError("Название не может быть пустым.")
                if len(clean_title) > MAX_TITLE_LENGTH:
                    raise ValueError("Название не должно быть длиннее 256 символов.")
                normalized_title = self._normalize_title(clean_title)
                if any(
                    item is not selected
                    and self._normalize_title(str(item.get("title", "")))
                    == normalized_title
                    for item in items
                ):
                    raise ValueError("Такой сериал уже существует.")
                updated["title"] = clean_title
            if tiktok_url is not None:
                self.validate_tiktok_url(tiktok_url)
                updated["tiktok_url"] = tiktok_url.strip()

            updated.update(metadata)
            if metadata.get("publication_status") == "published" and selected["publication_status"] == "hidden" and "published_at" not in metadata:
                updated["published_at"] = utc_now()
            updated_items = [
                updated if item is selected else item for item in items
            ]
            self._write_atomic({**payload, "series": updated_items})
            return dict(updated)

    @staticmethod
    def _validate_photo_file_id(photo_file_id: str) -> None:
        if (
            not isinstance(photo_file_id, str)
            or not photo_file_id.strip()
            or len(photo_file_id) > 2048
            or any(character.isspace() for character in photo_file_id)
        ):
            raise ValueError("Не удалось обработать фотографию. Отправьте её ещё раз.")

    def set_series_photo(
        self,
        series_id: int,
        photo_file_id: str | None,
        photo_url: str | None = None,
    ) -> dict:
        if photo_file_id is not None:
            self._validate_photo_file_id(photo_file_id)
        if photo_url is not None:
            self.validate_imgbb_url(photo_url)
        with self._lock:
            payload = self._load_catalog()
            items = payload["series"]
            selected = next(
                (
                    item
                    for item in items
                    if int(item.get("id", -1)) == int(series_id)
                ),
                None,
            )
            if selected is None:
                raise ValueError("Сериал не найден.")
            updated = {**selected, "updated_at": utc_now()}
            if photo_file_id is None:
                updated.pop("photo_file_id", None)
                updated.pop("photo_url", None)
            else:
                updated["photo_file_id"] = photo_file_id
                if photo_url is None:
                    updated.pop("photo_url", None)
                else:
                    updated["photo_url"] = photo_url
            self._write_atomic(
                {
                    **payload,
                    "series": [
                        updated if item is selected else item for item in items
                    ]
                }
            )
            return dict(updated)

    def delete_series(self, series_id: int) -> bool:
        with self._lock:
            payload = self._load_catalog()
            items = payload["series"]
            retained = []
            found = False
            for item in items:
                try:
                    matches = int(item.get("id", -1)) == int(series_id)
                except (TypeError, ValueError):
                    matches = False
                if matches:
                    found = True
                else:
                    retained.append(item)
            if not found:
                return False
            self._write_atomic({**payload, "series": retained})
            return True

    def count_series(self) -> int:
        return len(self.list_series())
