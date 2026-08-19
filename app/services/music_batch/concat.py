from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Sequence

from app.config import config


@dataclass(frozen=True)
class MediaSignature:
    video_codec: str
    width: int
    height: int
    frame_rate: str
    audio_codec: str
    sample_rate: int
    channel_layout: str


def _ffmpeg_executable() -> str:
    configured = getattr(config, "ffmpeg_path", "")
    if configured and Path(configured).is_file():
        return configured
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg executable was not found")
    return executable


def _ffprobe_executable() -> str:
    ffmpeg = _ffmpeg_executable()
    sibling = Path(ffmpeg).with_name(
        "ffprobe.exe" if Path(ffmpeg).suffix.lower() == ".exe" else "ffprobe"
    )
    if sibling.is_file():
        return str(sibling)
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe executable was not found")
    return executable


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"command failed with exit code {result.returncode}")
    return result


def probe_media_signature(path: Path) -> MediaSignature:
    result = _run(
        [
            _ffprobe_executable(),
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(Path(path)),
        ]
    )
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video is None:
        raise RuntimeError(f"no video stream found in {path}")
    if audio is None:
        raise RuntimeError(f"no audio stream found in {path}")
    return MediaSignature(
        video_codec=str(video.get("codec_name") or ""),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        frame_rate=str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"),
        audio_codec=str(audio.get("codec_name") or ""),
        sample_rate=int(audio.get("sample_rate") or 0),
        channel_layout=str(audio.get("channel_layout") or ""),
    )


def are_stream_copy_compatible(paths: Sequence[Path]) -> tuple[bool, str]:
    items = [Path(path) for path in paths]
    if not items:
        return False, "no completed videos"
    reference = probe_media_signature(items[0])
    for path in items[1:]:
        current = probe_media_signature(path)
        if current.video_codec != reference.video_codec:
            return False, f"video codec mismatch: {path.name}"
        if (current.width, current.height) != (reference.width, reference.height):
            return False, f"resolution mismatch: {path.name}"
        if current.frame_rate != reference.frame_rate:
            return False, f"frame rate mismatch: {path.name}"
        if current.audio_codec != reference.audio_codec:
            return False, f"audio codec mismatch: {path.name}"
        if current.sample_rate != reference.sample_rate:
            return False, f"audio sample rate mismatch: {path.name}"
        if current.channel_layout != reference.channel_layout:
            return False, f"audio channel layout mismatch: {path.name}"
    return True, "compatible"


def _concat_escape(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    return text.replace("'", "'\\''")


def concat_stream_copy(paths: Sequence[Path], output: Path) -> Path:
    items = [Path(path) for path in paths]
    if not items:
        raise ValueError("at least one video is required")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        prefix="music-batch-concat-",
        delete=False,
        dir=str(output.parent),
    ) as handle:
        list_path = Path(handle.name)
        for path in items:
            handle.write(f"file '{_concat_escape(path)}'\n")
    try:
        _run(
            [
                _ffmpeg_executable(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                str(output),
            ]
        )
    finally:
        list_path.unlink(missing_ok=True)
    if not output.exists():
        raise RuntimeError("ffmpeg completed without creating the compilation output")
    return output


def _fps_value(rate: str) -> float:
    try:
        value = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        value = 30.0
    if not math.isfinite(value) or value <= 0:
        return 30.0
    return value


def concat_reencode(
    paths: Sequence[Path], output: Path, codec: str
) -> Path:
    items = [Path(path) for path in paths]
    if not items:
        raise ValueError("at least one video is required")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    reference = probe_media_signature(items[0])
    width = reference.width or 1920
    height = reference.height or 1080
    fps = _fps_value(reference.frame_rate)

    command = [_ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y"]
    for path in items:
        command.extend(["-i", str(path)])

    filters: list[str] = []
    concat_inputs: list[str] = []
    for index in range(len(items)):
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.6f}[v{index}]"
        )
        filters.append(
            f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={len(items)}:v=1:a=1[outv][outa]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            codec,
            "-c:a",
            "aac",
            "-ar",
            "48000",
            str(output),
        ]
    )
    _run(command)
    if not output.exists():
        raise RuntimeError("ffmpeg completed without creating the re-encoded compilation")
    return output
