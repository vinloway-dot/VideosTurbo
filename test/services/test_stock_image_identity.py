from app.models.schema import MaterialInfo
from app.services import stock_images


class _ImageResponse:
    content = b"image-bytes"
    headers = {"content-type": "image/jpeg"}

    def raise_for_status(self):
        return None


def test_downloaded_stock_image_name_uses_asset_identity_not_download_order(
    monkeypatch, tmp_path
):
    item = MaterialInfo(
        provider="pexels",
        url="https://images.example.test/12345.jpg",
        source_info={"asset_id": "12345"},
    )
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

    assert first.name == "stock-image-12345.jpg"
    assert second.name == "stock-image-12345.jpg"
