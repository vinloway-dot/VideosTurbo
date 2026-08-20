from app.models.schema import MaterialInfo
from app.services import stock_images


class _ImageResponse:
    content = b"image-bytes"
    headers = {"content-type": "image/jpeg"}

    def raise_for_status(self):
        return None


def _item(provider: str = "pexels", asset_id: str = "12345") -> MaterialInfo:
    return MaterialInfo(
        provider=provider,
        url=f"https://images.example.test/{asset_id}.jpg",
        source_info={"asset_id": asset_id},
    )


def test_downloaded_stock_image_name_uses_provider_asset_identity_not_download_order(
    monkeypatch, tmp_path
):
    item = _item()
    monkeypatch.setattr(
        stock_images.requests,
        "get",
        lambda *args, **kwargs: _ImageResponse(),
    )

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    first = stock_images._download_image(item, first_dir, 1)
    second = stock_images._download_image(item, second_dir, 7)

    assert first.name == "stock-image-pexels-12345.jpg"
    assert second.name == "stock-image-pexels-12345.jpg"


def test_downloaded_stock_image_reuses_existing_provider_asset_without_redownloading(
    monkeypatch, tmp_path
):
    item = _item()
    existing = tmp_path / "stock-image-pexels-12345.jpg"
    existing.write_bytes(b"cached-image")

    def unexpected_request(*args, **kwargs):
        raise AssertionError("existing provider asset should be reused without HTTP")

    monkeypatch.setattr(stock_images.requests, "get", unexpected_request)

    result = stock_images._download_image(item, tmp_path, 99)

    assert result == existing
    assert result.read_bytes() == b"cached-image"


def test_same_asset_id_from_different_providers_does_not_share_cache(
    monkeypatch, tmp_path
):
    pexels = _item("pexels", "777")
    pixabay = _item("pixabay", "777")
    monkeypatch.setattr(
        stock_images.requests,
        "get",
        lambda *args, **kwargs: _ImageResponse(),
    )

    first = stock_images._download_image(pexels, tmp_path, 1)
    second = stock_images._download_image(pixabay, tmp_path, 1)

    assert first.name == "stock-image-pexels-777.jpg"
    assert second.name == "stock-image-pixabay-777.jpg"
    assert first != second
