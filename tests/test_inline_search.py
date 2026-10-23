from aiogram.enums import ParseMode
from aiogram.types import InlineQueryResultArticle
from unittest.mock import AsyncMock, MagicMock

import pytest

import handlers.inline_search as inline_search
from handlers.inline_search import build_inline_results


def test_inline_results_show_title_and_thumbnail_for_series_with_photo_url():
    results = build_inline_results(
        [
            {
                "id": 3,
                "title": "Сериал с фото",
                "tiktok_url": "https://www.tiktok.com/@a/video/3",
                "photo_file_id": "cached-photo",
                "photo_url": "https://i.ibb.co/abc123/poster.jpg",
            },
            {
                "id": 4,
                "title": "Сериал без фото",
                "tiktok_url": "https://www.tiktok.com/@b/video/4",
            },
        ]
    )

    photo_result, article_result = results
    assert isinstance(photo_result, InlineQueryResultArticle)
    assert photo_result.title == "Сериал с фото"
    assert photo_result.thumbnail_url == "https://i.ibb.co/abc123/poster.jpg"
    assert photo_result.input_message_content.parse_mode == ParseMode.HTML
    assert "Сериал с фото" in photo_result.input_message_content.message_text
    assert "poster.jpg" in photo_result.input_message_content.message_text
    assert (
        photo_result.input_message_content.link_preview_options.url
        == "https://i.ibb.co/abc123/poster.jpg"
    )
    assert isinstance(article_result, InlineQueryResultArticle)
    assert article_result.input_message_content.parse_mode == ParseMode.HTML
    assert "Сериал без фото" in article_result.input_message_content.message_text
    assert article_result.thumbnail_url is None


def test_legacy_photo_without_public_url_still_gets_visible_title():
    result = build_inline_results(
        [
            {
                "id": 5,
                "title": "Старый сериал",
                "tiktok_url": "https://www.tiktok.com/@a/video/5",
                "photo_file_id": "cached-photo",
            }
        ]
    )[0]

    assert isinstance(result, InlineQueryResultArticle)
    assert result.title == "Старый сериал"
    assert result.thumbnail_url is None
    assert "Постер не загружен" in result.description


def test_inline_result_uses_selected_locale_for_tiktok_action():
    result = build_inline_results(
        [
            {
                "id": 8,
                "title": "Series",
                "tiktok_url": "https://www.tiktok.com/@a/video/8",
            }
        ],
        language="en",
    )[0]

    assert result.reply_markup.inline_keyboard[0][0].text == "▶️ Open on TikTok"
    assert "Open the first episode on TikTok" in result.input_message_content.message_text


@pytest.mark.asyncio
async def test_empty_inline_query_lists_all_available_series(monkeypatch):
    from types import SimpleNamespace

    items = [
        {
            "id": 1,
            "title": "Available series",
            "tiktok_url": "https://www.tiktok.com/@a/video/1",
        }
    ]
    monkeypatch.setattr(
        inline_search.repository,
        "get_user_by_telegram_id",
        AsyncMock(return_value=SimpleNamespace(language="en", captcha_passed=True)),
    )
    monkeypatch.setattr(inline_search.series_service, "list_series", lambda: items)
    search = MagicMock(
        side_effect=AssertionError("search_series should not run for an empty query")
    )
    monkeypatch.setattr(
        inline_search.series_service,
        "search_series",
        search,
    )
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=123, language_code="en"),
        query="   ",
        answer=AsyncMock(),
    )

    await inline_search.search_inline(query)

    results = query.answer.await_args.kwargs["results"]
    assert len(results) == 1
    assert results[0].title == "Available series"
    assert query.answer.await_args.kwargs["is_personal"] is True
    search.assert_not_called()


@pytest.mark.asyncio
async def test_empty_inline_query_with_empty_catalog_shows_no_results(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        inline_search.repository,
        "get_user_by_telegram_id",
        AsyncMock(return_value=SimpleNamespace(language="en", captcha_passed=True)),
    )
    monkeypatch.setattr(inline_search.series_service, "list_series", lambda: [])
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=123, language_code="en"),
        query="",
        answer=AsyncMock(),
    )

    await inline_search.search_inline(query)

    results = query.answer.await_args.kwargs["results"]
    assert len(results) == 1
    assert results[0].title.startswith("🙈 No results found")
