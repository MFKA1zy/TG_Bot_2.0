from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import aiohttp

IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"


class ImageHostingError(RuntimeError):
    """Raised when an image cannot be uploaded to the configured host."""


async def upload_image_to_imgbb(image: bytes, api_key: str) -> str:
    if not api_key:
        raise ImageHostingError(
            "Не настроен IMGBB_API_KEY. Добавьте API-ключ ImgBB в локальный .env."
        )
    if not image:
        raise ImageHostingError("Получено пустое изображение.")

    form = aiohttp.FormData()
    form.add_field("key", api_key)
    form.add_field(
        "image",
        image,
        filename="series-poster.jpg",
        content_type="image/jpeg",
    )
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(IMGBB_UPLOAD_URL, data=form) as response:
                if response.status != 200:
                    raise ImageHostingError(
                        f"ImgBB отклонил загрузку (HTTP {response.status})."
                    )
                payload = await response.json(content_type=None)
    except ImageHostingError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ImageHostingError(
            "Не удалось подключиться к ImgBB. Проверьте интернет и повторите попытку."
        ) from exc
    except (ValueError, TypeError) as exc:
        raise ImageHostingError("ImgBB вернул некорректный ответ.") from exc

    if not isinstance(payload, dict):
        raise ImageHostingError("ImgBB вернул некорректный ответ.")
    data = payload.get("data")
    image_url = data.get("url") if isinstance(data, dict) else None
    if payload.get("success") is not True or not isinstance(image_url, str):
        raise ImageHostingError(
            "ImgBB не принял изображение. Проверьте API-ключ и попробуйте снова."
        )
    parsed_url = urlparse(image_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "i.ibb.co"
        or not parsed_url.path.strip("/")
    ):
        raise ImageHostingError("ImgBB вернул небезопасную ссылку на изображение.")
    return image_url
