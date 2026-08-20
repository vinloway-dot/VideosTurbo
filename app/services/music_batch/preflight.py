from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config import config
from app.services.music_batch.models import BatchSettings, SongItem
from app.utils import utils


class PreflightIssue(BaseModel):
    level: Literal["warning", "error"]
    code: str
    message: str


def _ffmpeg_executable() -> str | None:
    candidate = utils.get_ffmpeg_binary()
    if Path(candidate).is_file():
        return candidate
    resolved = shutil.which(candidate)
    if resolved:
        return resolved
    return None


def probe_encoder(codec: str) -> tuple[bool, str]:
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        return False, "ffmpeg executable was not found"

    with tempfile.TemporaryDirectory(prefix="mpt-encoder-probe-") as tmp_dir:
        output = Path(tmp_dir) / "probe.mp4"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=10",
            "-t",
            "0.3",
            "-c:v",
            codec,
            str(output),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    detail = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        return False, detail or f"ffmpeg exited with code {result.returncode}"
    return True, "ok"


def _configured_keys(provider: str) -> list[str]:
    value = config.app.get(f"{provider}_api_keys", [])
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _check_output_root(output_root: Path) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        test_file = output_root / ".music_batch_write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
    except OSError as exc:
        issues.append(
            PreflightIssue(
                level="error",
                code="output_unwritable",
                message=f"Output folder is not writable: {exc}",
            )
        )
        return issues

    try:
        usage = shutil.disk_usage(output_root)
        low_bytes = usage.free < 5 * 1024**3
        low_ratio = usage.total > 0 and usage.free / usage.total < 0.05
        if low_bytes or low_ratio:
            issues.append(
                PreflightIssue(
                    level="warning",
                    code="low_disk_space",
                    message=(
                        "Free disk space is low. Long music batches can require "
                        "substantial temporary and output storage."
                    ),
                )
            )
    except OSError:
        pass
    return issues


def _required_stock_sources(
    settings: BatchSettings, songs: list[SongItem]
) -> set[str]:
    required = {str(source).strip().lower() for source in settings.stock_sources}
    for song in songs:
        if song.override and song.override.stock_sources is not None:
            required.update(
                str(source).strip().lower()
                for source in song.override.stock_sources
                if str(source).strip()
            )
    return required


def run_preflight(
    settings: BatchSettings, songs: list[SongItem]
) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []

    if not songs:
        issues.append(
            PreflightIssue(
                level="error",
                code="no_inputs",
                message="At least one supported audio file is required.",
            )
        )

    for song in songs:
        path = Path(song.source_path)
        if not path.is_file() or not os.access(path, os.R_OK):
            issues.append(
                PreflightIssue(
                    level="error",
                    code="input_unreadable",
                    message=f"Input audio is missing or unreadable: {path}",
                )
            )

    output_root = Path(settings.output_root)
    issues.extend(_check_output_root(output_root))

    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        issues.append(
            PreflightIssue(
                level="error",
                code="ffmpeg_missing",
                message="FFmpeg executable was not found.",
            )
        )
    else:
        encoder_ok, encoder_detail = probe_encoder(settings.video_encoder)
        if not encoder_ok:
            issues.append(
                PreflightIssue(
                    level="error",
                    code="encoder_unavailable",
                    message=(
                        f"Selected encoder {settings.video_encoder} is unavailable: "
                        f"{encoder_detail}"
                    ),
                )
            )

    online_sources = {"pexels", "pixabay", "coverr"}
    for provider in sorted(_required_stock_sources(settings, songs) & online_sources):
        if not _configured_keys(provider):
            issues.append(
                PreflightIssue(
                    level="error",
                    code="missing_provider_key",
                    message=f"No API key is configured for {provider}.",
                )
            )

    return issues
