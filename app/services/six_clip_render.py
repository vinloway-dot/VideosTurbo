from __future__ import annotations

import os
import math
import shutil
import subprocess
from pathlib import Path

from loguru import logger

from app.models.schema import VideoAspect
from app.models.six_clip import SixClipPlan
from app.services import image_materials, video
from app.utils import utils


SEGMENT_DURATION_SECONDS = 10.0


class SixClipRenderError(RuntimeError):
    pass


def _video_filter(aspect: VideoAspect | str) -> str:
    width, height = VideoAspect(aspect).to_resolution()
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        "setsar=1,"
        f"fps={video.fps}"
    )


def _run_ffmpeg_normalize(
    source_path: str,
    output_path: str,
    *,
    aspect: VideoAspect | str,
    threads: int,
    codec: str,
) -> None:
    command = [
        utils.get_ffmpeg_binary(),
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        source_path,
        "-t",
        f"{SEGMENT_DURATION_SECONDS:.3f}",
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        _video_filter(aspect),
        "-c:v",
        codec,
        "-threads",
        str(threads or 2),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(video.fps),
        output_path,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg normalize failed").strip()
        raise SixClipRenderError(detail)


def _normalize_video_segment(
    source_path: str,
    output_path: str,
    *,
    aspect: VideoAspect | str,
    threads: int,
) -> str:
    configured_codec = video._get_configured_video_codec()
    try:
        _run_ffmpeg_normalize(
            source_path,
            output_path,
            aspect=aspect,
            threads=threads,
            codec=configured_codec,
        )
    except SixClipRenderError as first_error:
        if configured_codec == "libx264":
            raise
        logger.warning(
            "six-clip hardware video normalization failed; retrying with libx264: "
            f"{type(first_error).__name__}: {first_error}"
        )
        _run_ffmpeg_normalize(
            source_path,
            output_path,
            aspect=aspect,
            threads=threads,
            codec="libx264",
        )
        disable = getattr(video, "_disable_runtime_video_codec", None)
        if callable(disable):
            disable(configured_codec, str(first_error))
    return output_path


def prepare_six_clip_timeline(
    task_id: str,
    plan: SixClipPlan,
    *,
    video_aspect: VideoAspect | str,
    image_motion="random",
    threads: int = 2,
    output_dir: str | os.PathLike | None = None,
) -> list[str]:
    destination = Path(output_dir or (Path(utils.task_dir(task_id)) / "six-clips"))
    destination.mkdir(parents=True, exist_ok=True)
    prepared: list[str] = []

    for segment in plan.segments:
        source = Path(segment.media_path)
        if not source.is_file():
            raise SixClipRenderError(f"clip {segment.index} media file is missing")

        if segment.media_kind == "image":
            image_outputs = image_materials.prepare_image_clips(
                [str(source)],
                output_dir=destination,
                duration=int(SEGMENT_DURATION_SECONDS),
                motion=image_motion,
                aspect=video_aspect,
                codec=video._get_configured_video_codec(),
            )
            if not image_outputs:
                raise SixClipRenderError(
                    f"failed to prepare image media for clip {segment.index}"
                )
            generated = Path(image_outputs[0])
            stable_output = destination / f"six-clip-{segment.index:03d}.mp4"
            if generated.resolve() != stable_output.resolve():
                stable_output.unlink(missing_ok=True)
                shutil.move(str(generated), str(stable_output))
            prepared.append(str(stable_output))
            continue

        if segment.media_kind != "video":
            raise SixClipRenderError(
                f"clip {segment.index} has unsupported media kind {segment.media_kind!r}"
            )

        output_path = destination / f"six-clip-{segment.index:03d}.mp4"
        output_path.unlink(missing_ok=True)
        prepared.append(
            _normalize_video_segment(
                str(source),
                str(output_path),
                aspect=video_aspect,
                threads=threads,
            )
        )

    return prepared


def probe_video_duration(video_path: str | os.PathLike) -> float:
    """Measure an output video and always release the MoviePy reader."""
    clip = None
    try:
        clip = video._open_video_clip_quietly(str(video_path), audio=False)
        duration = float(clip.duration)
    except Exception as exc:
        raise SixClipRenderError("failed to measure combined timeline duration") from exc
    finally:
        if clip is not None:
            close = getattr(clip, "close", None)
            if callable(close):
                close()
            else:
                video.close_clip(clip)
    if not math.isfinite(duration) or duration <= 0:
        raise SixClipRenderError("combined timeline duration is invalid")
    return duration


def concat_six_clip_timeline(
    clip_paths: list[str],
    output_file: str | os.PathLike,
    *,
    timeline_duration_sec: float,
    threads: int = 2,
    duration_tolerance_sec: float = 0.5,
) -> str:
    if not clip_paths:
        raise SixClipRenderError("timeline requires prepared clips")
    expected_duration = float(timeline_duration_sec)
    tolerance = float(duration_tolerance_sec)
    if not math.isfinite(expected_duration) or expected_duration <= 0:
        raise SixClipRenderError("timeline duration must be a finite positive number")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise SixClipRenderError("duration tolerance must be finite and non-negative")

    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    video.concat_video_clips_with_ffmpeg(
        clip_files=list(clip_paths),
        output_file=str(output),
        threads=threads or 2,
        output_dir=str(output.parent),
        max_duration=expected_duration,
    )
    if not output.is_file() or output.stat().st_size <= 0:
        raise SixClipRenderError("failed to concatenate timeline")
    actual_duration = probe_video_duration(output)
    if abs(actual_duration - expected_duration) > tolerance:
        raise SixClipRenderError(
            f"combined timeline duration is {actual_duration:.3f}s; "
            f"expected {expected_duration:.1f}s within {tolerance:.1f}s"
        )
    return str(output)
