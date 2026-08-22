import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.cloud_agent.errors import MediaValidationError
from app.utils import utils


_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]+")


@dataclass(frozen=True)
class MediaProbe:
    path: Path
    size_bytes: int
    duration: float
    has_audio: bool
    has_video: bool
    audio_codec: str
    video_codec: str
    width: int | None
    height: int | None


def _ffprobe_binary() -> str:
    ffmpeg = str(utils.get_ffmpeg_binary() or "")
    ffmpeg_path = Path(ffmpeg)
    if ffmpeg and (ffmpeg_path.is_absolute() or ffmpeg_path.parent != Path(".")):
        sibling_name = "ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"
        sibling = ffmpeg_path.with_name(sibling_name)
        if sibling.exists():
            return str(sibling)
    return shutil.which("ffprobe") or "ffprobe"


def _sanitize_diagnostic(value: str) -> str:
    sanitized = _URL_QUERY_RE.sub(r"\1", str(value or "")).strip()
    return sanitized[:1000]


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe_media(path: Path) -> MediaProbe:
    media_path = Path(path)
    if not media_path.exists():
        raise MediaValidationError(f"media file does not exist: {media_path}")

    command = [
        _ffprobe_binary(),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(media_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = _sanitize_diagnostic(result.stderr) or "unknown error"
        raise MediaValidationError(f"ffprobe failed: {detail}")

    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MediaValidationError("invalid ffprobe JSON") from exc

    streams = payload.get("streams", []) if isinstance(payload, dict) else []
    audio_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )
    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    format_info = payload.get("format", {}) if isinstance(payload, dict) else {}
    duration = _as_float(format_info.get("duration") if isinstance(format_info, dict) else None)

    return MediaProbe(
        path=media_path,
        size_bytes=media_path.stat().st_size,
        duration=duration,
        has_audio=audio_stream is not None,
        has_video=video_stream is not None,
        audio_codec=str(audio_stream.get("codec_name") or "") if audio_stream else "",
        video_codec=str(video_stream.get("codec_name") or "") if video_stream else "",
        width=_as_int(video_stream.get("width")) if video_stream else None,
        height=_as_int(video_stream.get("height")) if video_stream else None,
    )


def _validate_duration(
    probe: MediaProbe,
    *,
    min_duration: float | None,
    max_duration: float | None,
) -> None:
    if min_duration is not None and probe.duration < min_duration:
        raise MediaValidationError(
            f"media duration {probe.duration:.3f}s is below minimum {min_duration:.3f}s"
        )
    if max_duration is not None and probe.duration > max_duration:
        raise MediaValidationError(
            f"media duration {probe.duration:.3f}s exceeds maximum {max_duration:.3f}s"
        )


def validate_audio(
    path: Path,
    *,
    min_duration: float,
    max_duration: float | None = None,
) -> MediaProbe:
    probe = probe_media(path)
    if not probe.has_audio:
        raise MediaValidationError("media does not contain an audio stream")
    if not probe.audio_codec:
        raise MediaValidationError("media audio codec is missing")
    _validate_duration(
        probe,
        min_duration=min_duration,
        max_duration=max_duration,
    )
    return probe


def validate_video(
    path: Path,
    *,
    require_audio: bool = False,
    min_size_bytes: int = 1,
    min_duration: float | None = None,
    max_duration: float | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> MediaProbe:
    probe = probe_media(path)
    if probe.size_bytes < min_size_bytes:
        raise MediaValidationError(
            f"media is below minimum size: {probe.size_bytes} < {min_size_bytes} bytes"
        )
    if not probe.has_video:
        raise MediaValidationError("media does not contain a video stream")
    if not probe.video_codec:
        raise MediaValidationError("media video codec is missing")
    if require_audio and not probe.has_audio:
        raise MediaValidationError("media does not contain an audio stream")
    if require_audio and not probe.audio_codec:
        raise MediaValidationError("media audio codec is missing")
    _validate_duration(
        probe,
        min_duration=min_duration,
        max_duration=max_duration,
    )
    if expected_width is not None and probe.width != expected_width:
        raise MediaValidationError(
            f"media resolution is {probe.width}x{probe.height}; expected width {expected_width}"
        )
    if expected_height is not None and probe.height != expected_height:
        raise MediaValidationError(
            f"media resolution is {probe.width}x{probe.height}; expected height {expected_height}"
        )
    return probe
