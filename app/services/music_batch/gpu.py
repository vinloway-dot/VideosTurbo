from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Sequence

from loguru import logger

from app.services import video as video_service
from app.utils import utils

NVENC_ENCODERS = frozenset({"h264_nvenc", "hevc_nvenc", "av1_nvenc"})
_active_gpu_index: ContextVar[int | None] = ContextVar(
    "music_batch_nvidia_gpu_index", default=None
)
_music_batch_gpu_context: ContextVar[bool] = ContextVar(
    "music_batch_gpu_context_active", default=False
)
_hook_lock = threading.Lock()
_hooks_installed = False
_original_write_videofile = video_service._write_videofile_with_codec_fallback
_original_concat = video_service.concat_video_clips_with_ffmpeg


def is_nvenc_encoder(codec: object) -> bool:
    return str(codec or "").strip().lower() in NVENC_ENCODERS


def detect_nvidia_gpu_indices() -> list[int]:
    """Return NVIDIA GPU indices visible to this process, or an empty list."""

    executable = shutil.which("nvidia-smi")
    if not executable:
        return []

    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=index",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0:
        return []

    indices: list[int] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            index = int(value)
        except ValueError:
            continue
        if index >= 0 and index not in indices:
            indices.append(index)
    return indices


def build_gpu_assignments(
    song_indices: Sequence[int], codec: str, gpu_indices: Sequence[int]
) -> dict[int, int | None]:
    """Assign NVENC songs to visible GPUs in deterministic round-robin order."""

    songs = list(song_indices)
    if not is_nvenc_encoder(codec):
        return {song_index: None for song_index in songs}

    gpus = list(dict.fromkeys(int(index) for index in gpu_indices if int(index) >= 0))
    if not gpus:
        return {song_index: None for song_index in songs}

    return {
        song_index: gpus[position % len(gpus)]
        for position, song_index in enumerate(songs)
    }


def current_gpu_index() -> int | None:
    return _active_gpu_index.get()


def music_batch_gpu_context_active() -> bool:
    return _music_batch_gpu_context.get()


@contextmanager
def nvenc_gpu_context(gpu_index: int | None) -> Iterator[None]:
    """Bind Music Batch NVENC calls to one GPU and disable silent CPU fallback."""

    context_token = _music_batch_gpu_context.set(True)
    gpu_token = None
    if gpu_index is not None:
        gpu_token = _active_gpu_index.set(int(gpu_index))
    try:
        yield
    finally:
        if gpu_token is not None:
            _active_gpu_index.reset(gpu_token)
        _music_batch_gpu_context.reset(context_token)


def _gpu_ffmpeg_params(codec: str) -> list[str]:
    gpu_index = current_gpu_index()
    if gpu_index is None or not is_nvenc_encoder(codec):
        return []
    return ["-gpu", str(gpu_index)]


def _gpu_aware_write_videofile(clip, output_file: str, codec: str, **kwargs):
    """Use scheduled NVENC and fail closed for Music Batch hardware encoding."""

    if not music_batch_gpu_context_active() or not is_nvenc_encoder(codec):
        return _original_write_videofile(clip, output_file, codec, **kwargs)

    effective_codec = video_service._get_effective_video_codec(codec)
    if not is_nvenc_encoder(effective_codec):
        raise RuntimeError(
            f"Music Batch requested {codec}, but it is unavailable at runtime; "
            "CPU fallback is disabled"
        )

    hardware_kwargs = dict(kwargs)
    ffmpeg_params = list(hardware_kwargs.get("ffmpeg_params") or [])
    ffmpeg_params.extend(_gpu_ffmpeg_params(effective_codec))
    if ffmpeg_params:
        hardware_kwargs["ffmpeg_params"] = ffmpeg_params

    try:
        clip.write_videofile(
            output_file,
            codec=effective_codec,
            **hardware_kwargs,
        )
        return effective_codec
    except Exception as exc:
        logger.error(
            "Music Batch NVENC write failed; refusing libx264 fallback: "
            f"codec={effective_codec}, gpu={current_gpu_index()}, error={exc}"
        )
        raise


def _gpu_aware_concat(
    clip_files: list[str],
    output_file: str,
    threads: int,
    output_dir: str,
    max_duration: float | None = None,
):
    """Use the scheduled NVIDIA device and fail closed inside Music Batch."""

    if not music_batch_gpu_context_active():
        return _original_concat(
            clip_files,
            output_file,
            threads,
            output_dir,
            max_duration,
        )

    requested_codec = video_service._get_configured_video_codec()
    if not is_nvenc_encoder(requested_codec):
        return _original_concat(
            clip_files,
            output_file,
            threads,
            output_dir,
            max_duration,
        )

    effective_codec = video_service._get_effective_video_codec(requested_codec)
    if not is_nvenc_encoder(effective_codec):
        raise RuntimeError(
            f"Music Batch requested {requested_codec}, but it is unavailable at runtime; "
            "CPU fallback is disabled"
        )

    concat_list_file = str(Path(output_dir) / "ffmpeg-concat-list.txt")
    with open(concat_list_file, "w", encoding="utf-8") as fp:
        for clip_file in clip_files:
            formatted = video_service._format_ffmpeg_concat_path(clip_file)
            fp.write(f"file '{formatted}'\n")

    command = [
        utils.get_ffmpeg_binary(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list_file,
        "-c:v",
        effective_codec,
    ]
    command.extend(_gpu_ffmpeg_params(effective_codec))
    command.extend(
        [
            "-threads",
            str(threads or 2),
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if max_duration is not None and max_duration > 0:
        command.extend(["-t", f"{max_duration:.3f}"])
    command.append(output_file)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(detail or "ffmpeg concat failed")
        return effective_codec
    except Exception as exc:
        logger.error(
            "Music Batch NVENC concat failed; refusing libx264 fallback: "
            f"codec={effective_codec}, gpu={current_gpu_index()}, error={exc}"
        )
        raise
    finally:
        video_service.delete_files(concat_list_file)


def install_video_gpu_hooks() -> None:
    """Install context-aware hooks once; calls outside Music Batch stay unchanged."""

    global _hooks_installed
    with _hook_lock:
        if _hooks_installed:
            return
        video_service._write_videofile_with_codec_fallback = _gpu_aware_write_videofile
        video_service.concat_video_clips_with_ffmpeg = _gpu_aware_concat
        _hooks_installed = True
        logger.debug("installed Music Batch NVENC GPU scheduling hooks")
