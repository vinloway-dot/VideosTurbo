from pathlib import Path

import pytest

from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services import six_clip_media


class FakeResponse:
    def __init__(self, body: bytes, content_type: str, status_code: int = 200):
        self._body = body
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index : index + chunk_size]

    def close(self):
        return None


class OversizeHeaderResponse:
    def __init__(self):
        self.headers = {"Content-Type": "video/mp4", "Content-Length": "1000"}
        self.iterated = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1024):
        self.iterated = True
        raise AssertionError("oversized response body must not be read")

    def close(self):
        return None


class SignedUrlErrorResponse:
    def __init__(self):
        self.headers = {}

    def raise_for_status(self):
        raise six_clip_media.requests.RequestException(
            "403 Client Error for url: "
            "https://flow-content.google/video/id?Signature=secret&Expires=1"
        )

    def close(self):
        return None


@pytest.fixture
def mp4_bytes():
    # ISO-BMFF/MP4 files identify themselves with an ftyp box near the beginning.
    return b"\x00\x00\x00\x18ftypisom" + (b"x" * 128)


def test_google_flow_style_url_without_extension_is_imported_from_content_type(
    monkeypatch, tmp_path, mp4_bytes
):
    signed_url = (
        "https://flow-content.google/video/example-id?Expires=1787351197"
        "&KeyName=labs-flow-prod-cdn-key&Signature=secret"
    )
    monkeypatch.setattr(
        six_clip_media.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(mp4_bytes, "video/mp4"),
    )

    imported = six_clip_media.import_media_url(signed_url, tmp_path, clip_index=3)

    assert imported.media_kind == "video"
    assert imported.local_path.endswith("clip-03.mp4")
    assert Path(imported.local_path).read_bytes() == mp4_bytes
    assert "Signature" not in imported.local_path


def test_import_rejects_html_even_when_url_looks_like_media(monkeypatch, tmp_path):
    monkeypatch.setattr(
        six_clip_media.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(b"<html>login</html>", "text/html"),
    )

    with pytest.raises(six_clip_media.SixClipMediaError, match="supported media"):
        six_clip_media.import_media_url(
            "https://example.com/video.mp4?token=secret",
            tmp_path,
            clip_index=1,
        )


def test_import_rejects_html_body_even_when_content_type_claims_mp4(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        six_clip_media.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(b"<html>login</html>", "video/mp4"),
    )

    with pytest.raises(six_clip_media.SixClipMediaError, match="supported media"):
        six_clip_media.import_media_url(
            "https://example.com/download?Signature=secret",
            tmp_path,
            clip_index=1,
        )


def test_import_rejects_oversize_content_length_before_reading_body(
    monkeypatch, tmp_path
):
    response = OversizeHeaderResponse()
    monkeypatch.setattr(
        six_clip_media.requests,
        "get",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(six_clip_media.SixClipMediaError, match="maximum allowed size"):
        six_clip_media.import_media_url(
            "https://example.com/large-video",
            tmp_path,
            clip_index=1,
            max_bytes=10,
        )

    assert response.iterated is False


def test_request_failure_does_not_expose_signed_url_query(monkeypatch, tmp_path):
    monkeypatch.setattr(
        six_clip_media.requests,
        "get",
        lambda *args, **kwargs: SignedUrlErrorResponse(),
    )

    with pytest.raises(six_clip_media.SixClipMediaError) as exc_info:
        six_clip_media.import_media_url(
            "https://flow-content.google/video/id?Signature=secret&Expires=1",
            tmp_path,
            clip_index=1,
        )

    message = str(exc_info.value)
    assert "secret" not in message
    assert "Signature=" not in message
    assert "Expires=" not in message


def test_import_rejects_non_http_scheme(tmp_path):
    with pytest.raises(six_clip_media.SixClipMediaError, match="HTTP or HTTPS"):
        six_clip_media.import_media_url(
            "file:///etc/passwd",
            tmp_path,
            clip_index=1,
        )


def test_redact_media_url_removes_signed_query_values():
    url = (
        "https://flow-content.google/video/abc?Expires=1&KeyName=name&Signature=secret"
    )
    redacted = six_clip_media.redact_media_url(url)

    assert redacted == "https://flow-content.google/video/abc?<redacted>"
    assert "secret" not in redacted
    assert "Expires=1" not in redacted


def test_save_uploaded_media_accepts_image_and_video(tmp_path):
    image = six_clip_media.save_uploaded_media(
        "photo.PNG", b"\x89PNG\r\n\x1a\n" + b"x" * 32, tmp_path, clip_index=1
    )
    video = six_clip_media.save_uploaded_media(
        "movie.mp4", b"\x00\x00\x00\x18ftypisom" + b"x" * 32, tmp_path, clip_index=2
    )

    assert image.media_kind == "image"
    assert image.local_path.endswith("clip-01.png")
    assert video.media_kind == "video"
    assert video.local_path.endswith("clip-02.mp4")


def test_validate_ready_media_reports_all_missing_or_deleted_clips(tmp_path):
    existing = tmp_path / "clip-01.mp4"
    existing.write_bytes(b"x")
    segments = []
    for index in range(1, 7):
        start = (index - 1) * 10
        end = index * 10
        if index == 1:
            media_kind = "video"
            media_path = str(existing)
        else:
            media_kind = ""
            media_path = ""
        segments.append(
            SixClipSegment(
                index=index,
                start_sec=start,
                end_sec=end,
                title=f"Clip {index}",
                narration_context="n",
                video_prompt="p",
                media_kind=media_kind,
                media_path=media_path,
            )
        )
    plan = SixClipPlan(target_words=130, segments=segments)

    missing = six_clip_media.validate_ready_media(plan)

    assert missing == [2, 3, 4, 5, 6]
