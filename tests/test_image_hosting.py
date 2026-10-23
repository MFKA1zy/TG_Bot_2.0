import pytest

from services.image_hosting import ImageHostingError, upload_image_to_imgbb


@pytest.mark.asyncio
async def test_imgbb_upload_requires_api_key():
    with pytest.raises(ImageHostingError, match="IMGBB_API_KEY"):
        await upload_image_to_imgbb(b"image-bytes", "")


@pytest.mark.asyncio
async def test_imgbb_upload_returns_direct_image_url(monkeypatch):
    from services import image_hosting

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, *, content_type):
            assert content_type is None
            return {
                "success": True,
                "data": {"url": "https://i.ibb.co/abc123/poster.jpg"},
            }

    class FakeSession:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, *, data):
            assert url == image_hosting.IMGBB_UPLOAD_URL
            assert data is not None
            return FakeResponse()

    monkeypatch.setattr(image_hosting.aiohttp, "ClientSession", FakeSession)

    result = await upload_image_to_imgbb(b"image-bytes", "test-api-key")

    assert result == "https://i.ibb.co/abc123/poster.jpg"
