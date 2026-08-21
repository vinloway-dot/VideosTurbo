from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from loguru import logger

from app.models.six_clip import SixClipPlan


MAX_MEDIA_BYTES = 500 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_TIMEOUT = (10, 60)

_CONTENT_TYPE_MAP = {
    "video/mp4": ("video", ".mp4"),
    "video/webm": ("video", ".webm"),
    "video/quicktime": ("video", ".mov"),
    "image/jpeg": ("image", ".jpg"),
    "image/jpg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/webp": ("image", ".webp"),
}
_EXTENSION_MAP = {
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
}


class SixClipMediaError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedMedia:
    media_kind: str
    local_path: str


def redact_media_url(url: str) -> str:
    """Return a log-safe URL that never reveals signed query values."""
    parts = urlsplit(str(url or "").strip())
    query = "<redacted>" if parts.query else ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _validate_remote_url(url: str) -> str:
    value = str(url or "").strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise SixClipMediaError("media URL must use HTTP or HTTPS")
    return value


def _detect_magic(header: bytes) -> tuple[str, str] | None:
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video", ".mp4"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "video", ".webm"
    if header.startswith(b"\xff\xd8\xff"):
        return "image", ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", ".png"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image", ".webp"
    return None


def _detect_media_type(content_type: str, header: bytes) -> tuple[str, str]:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    mapped = _CONTENT_TYPE_MAP.get(normalized)
    if mapped:
        return mapped
    detected = _detect_magic(header)
    if detected:
        return detected
    raise SixClipMediaError("URL did not return supported media content")


def _destination_path(destination_dir: str | os.PathLike, clip_index: int, suffix: str) -> Path:
    if clip_index < 1 or clip_index > 6:
        raise SixClipMediaError("clip_index must be between 1 and 6")
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    return destination / f"clip-{clip_index:02d}{suffix.lower()}"


def import_media_url(
    url: str,
    destination_dir: str | os.PathLike,
    clip_index: int,
    *,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> ImportedMedia:
    """Download a direct media URL immediately and return only the local copy.

    The URL suffix is intentionally ignored. Google Flow and other signed CDNs
    commonly return `/video/<id>?Signature=...` URLs with no `.mp4` suffix.
    """

    value = _validate_remote_url(url)
    safe_url = redact_media_url(value)
    logger.info(f"import six-clip media: clip={clip_index}, url={safe_url}")
    response = None
    temp_path: Path | None = None
    try:
        response = requests.get(
            value,
            stream=True,
            timeout=DOWNLOAD_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > int(max_bytes):
                    raise SixClipMediaError("media URL exceeds the maximum allowed size")
            except ValueError:
                pass

        iterator = response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE)
        first_chunk = next(iterator, b"")
        if not first_chunk:
            raise SixClipMediaError("media URL returned an empty response")
        media_kind, suffix = _detect_media_type(
            response.headers.get("Content-Type", ""),
            first_chunk[:64],
        )
        output_path = _destination_path(destination_dir, clip_index, suffix)
        temp_path = output_path.with_suffix(output_path.suffix + ".part")
        total = 0
        with open(temp_path, "wb") as handle:
            for chunk in (first_chunk,):
                total += len(chunk)
                if total > max_bytes:
                    raise SixClipMediaError("media URL exceeds the maximum allowed size")
                handle.write(chunk)
            for chunk in iterator:
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise SixClipMediaError("media URL exceeds the maximum allowed size")
                handle.write(chunk)
        if total <= 0:
            raise SixClipMediaError("media URL returned an empty response")
        os.replace(temp_path, output_path)
        temp_path = None
        return ImportedMedia(media_kind=media_kind, local_path=str(output_path))
    except SixClipMediaError:
        raise
    except requests.RequestException as exc:
        raise SixClipMediaError(f"failed to download media URL: {exc}") from exc
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith("HTTP "):
            raise SixClipMediaError(f"failed to download media URL: {exc}") from exc
        raise
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _read_upload_bytes(upload) -> bytes:
    if isinstance(upload, (bytes, bytearray)):
        return bytes(upload)
    getbuffer = getattr(upload, "getbuffer", None)
    if callable(getbuffer):
        return bytes(getbuffer())
    read = getattr(upload, "read", None)
    if callable(read):
        data = read()
        return bytes(data)
    raise SixClipMediaError("uploaded media is not readable")


def save_uploaded_media(
    filename: str,
    upload,
    destination_dir: str | os.PathLike,
    clip_index: int,
    *,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> ImportedMedia:
    suffix = Path(str(filename or "")).suffix.lower()
    media_kind = _EXTENSION_MAP.get(suffix)
    if not media_kind:
        raise SixClipMediaError("unsupported uploaded media type")

    data = _read_upload_bytes(upload)
    if not data:
        raise SixClipMediaError("uploaded media is empty")
    if len(data) > max_bytes:
        raise SixClipMediaError("uploaded media exceeds the maximum allowed size")

    magic = _detect_magic(data[:64])
    if magic is None:
        raise SixClipMediaError("uploaded file does not contain supported media")
    detected_kind, detected_suffix = magic
    if detected_kind != media_kind:
        raise SixClipMediaError("uploaded file extension does not match its media content")

    output_suffix = detected_suffix if detected_kind == "image" else suffix
    output_path = _destination_path(destination_dir, clip_index, output_suffix)
    output_path.write_bytes(data)
    return ImportedMedia(media_kind=detected_kind, local_path=str(output_path))


def validate_ready_media(plan: SixClipPlan) -> list[int]:
    missing: list[int] = []
    for segment in plan.segments:
        path = Path(segment.media_path) if segment.media_path else None
        if (
            segment.media_kind not in {"video", "image"}
            or path is None
            or not path.is_file()
            or path.stat().st_size <= 0
        ):
            missing.append(segment.index)
    return missing


def missing_media_message(plan: SixClipPlan) -> str:
    missing = set(validate_ready_media(plan))
    ranges = [
        f"Clip {segment.index} ({segment.start_sec}–{segment.end_sec}s)"
        for segment in plan.segments
        if segment.index in missing
    ]
    return ", ".join(ranges)


def materialize_plan_for_task(
    plan: SixClipPlan,
    task_dir: str | os.PathLike,
) -> SixClipPlan:
    """Copy all ready media into the task directory and return task-local paths."""
    missing = validate_ready_media(plan)
    if missing:
        raise SixClipMediaError(
            "missing media: " + missing_media_message(plan)
        )

    destination = Path(task_dir) / "six-clip-sources"
    destination.mkdir(parents=True, exist_ok=True)
    segments = []
    for segment in plan.segments:
        source = Path(segment.media_path)
        suffix = source.suffix.lower()
        target = destination / f"clip-{segment.index:02d}{suffix}"
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        segments.append(segment.model_copy(update={"media_path": str(target)}))
    return plan.model_copy(update={"segments": segments})
