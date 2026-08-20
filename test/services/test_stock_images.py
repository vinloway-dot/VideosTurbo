from app.models.schema import VideoAspect
from app.services import stock_images


class _Response:
    def __init__(self, payload, *, content=b"image-bytes", headers=None, status_code=200):
        self._payload = payload
        self.content = content
        self.headers = headers or {"content-type": "image/jpeg"}
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_search_images_pexels_filters_orientation_and_keeps_source_metadata(monkeypatch):
    payload = {
        "photos": [
            {
                "id": 11,
                "width": 2400,
                "height": 1350,
                "url": "https://www.pexels.com/photo/landscape-11/",
                "photographer": "Alice",
                "photographer_url": "https://www.pexels.com/@alice/",
                "src": {"original": "https://images.pexels.com/photos/11/original.jpg"},
            },
            {
                "id": 12,
                "width": 1080,
                "height": 1920,
                "url": "https://www.pexels.com/photo/portrait-12/",
                "photographer": "Bob",
                "photographer_url": "https://www.pexels.com/@bob/",
                "src": {"original": "https://images.pexels.com/photos/12/original.jpg"},
            },
        ]
    }
    monkeypatch.setattr(stock_images, "get_api_key", lambda _key: "pexels-key")
    monkeypatch.setattr(stock_images.requests, "get", lambda *args, **kwargs: _Response(payload))

    items = stock_images.search_images_pexels("ocean", VideoAspect.landscape)

    assert len(items) == 1
    item = items[0]
    assert item.provider == "pexels"
    assert item.url.endswith("original.jpg")
    assert item.source_info["asset_id"] == "11"
    assert item.source_info["search_term"] == "ocean"
    assert item.source_info["rendition"]["width"] == 2400
    assert item.source_info["rendition"]["height"] == 1350


def test_search_images_pixabay_filters_orientation_and_keeps_source_metadata(monkeypatch):
    payload = {
        "hits": [
            {
                "id": 21,
                "pageURL": "https://pixabay.com/photos/forest-21/",
                "user": "Carol",
                "user_id": 99,
                "largeImageURL": "https://pixabay.com/get/forest-21.jpg",
                "imageWidth": 1920,
                "imageHeight": 1080,
            },
            {
                "id": 22,
                "pageURL": "https://pixabay.com/photos/tall-22/",
                "user": "Dan",
                "user_id": 100,
                "largeImageURL": "https://pixabay.com/get/tall-22.jpg",
                "imageWidth": 1080,
                "imageHeight": 1920,
            },
        ]
    }
    monkeypatch.setattr(stock_images, "get_api_key", lambda _key: "pixabay-key")
    monkeypatch.setattr(stock_images.requests, "get", lambda *args, **kwargs: _Response(payload))

    items = stock_images.search_images_pixabay("forest", VideoAspect.portrait)

    assert len(items) == 1
    item = items[0]
    assert item.provider == "pixabay"
    assert item.url.endswith("tall-22.jpg")
    assert item.source_info["asset_id"] == "22"
    assert item.source_info["search_term"] == "forest"


def test_square_image_search_accepts_crop_friendly_non_square_assets(monkeypatch):
    payload = {
        "photos": [
            {
                "id": 31,
                "width": 2400,
                "height": 1600,
                "url": "https://www.pexels.com/photo/crop-31/",
                "photographer": "Eve",
                "photographer_url": "https://www.pexels.com/@eve/",
                "src": {"original": "https://images.pexels.com/photos/31/original.jpg"},
            }
        ]
    }
    monkeypatch.setattr(stock_images, "get_api_key", lambda _key: "pexels-key")
    monkeypatch.setattr(stock_images.requests, "get", lambda *args, **kwargs: _Response(payload))

    items = stock_images.search_images_pexels("nature", VideoAspect.square)

    assert len(items) == 1
