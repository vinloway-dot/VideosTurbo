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


@contextmanager
def nvenc_gpu_context(gpu_index: int | None) -> Iterator[None]:
    """Bind NVENC calls in the current execution context to one NVIDIA GPU."""

    if gpu_index is None:
        yield
        return

    token = _active_gpu_index.set(int(gpu_index))
    try:
        yield
    finally:
        _active_gpu_index.reset(token)


def _gpu_ffmpeg_params(codec: str) -> list[str]:
    gpu_index = current_gpu_index()
    if gpu_index is None or not is_nvenc_encoder(codec):
        return []
    return ["-gpu", str(gpu_index)]


def _gpu_aware_write_videofile(clip, output_file: str, codec: str, **kwargs):
    """Preserve the original writer, adding -gpu only for scheduled NVENC jobs."""

    gpu_index = current_gpu_index()
    if gpu_index is None or not is_nvenc_encoder(codec):
        return _original_write_videofile(clip, output_file, codec, **kwargs)

    effective_codec = video_service._get_effective_video_codec(codec)
    if not is_nvenc_encoder(effective_codec):
        return _original_write_videofile(clip, output_file, codec, **kwargs)

    hardware_kwargs = dict(kwargs)
    ffmpeg_params = list(hardware_kwargs.get("ffmpeg_params") or [])
    ffmpeg_params.extend(_gpu_ffmpeg_params(effective_codec))
    hardware_kwargs["ffmpeg_params"] = ffmpeg_params

    try:
        clip.write_videofile(
            output_file,
            codec=effective_codec,
            **hardware_kwargs,
        )
        return effective_codec
    except Exception as exc:
        if effective_codec == video_service._DEFAULT_VIDEO_CODEC:
            raise
        # The CPU fallback must not inherit the NVENC-only -gpu parameter.
        return video_service._fallback_write_videofile(
            clip,
            output_file,
            effective_codec,
            str(exc),
            **kwargs,
        )


def _gpu_aware_concat(
    clip_files: list[str],
    output_file: str,
    threads: int,
    output_dir: str,
    max_duration: float | None = None,
):
    """Use the scheduled NVIDIA device for the direct FFmpeg concat encode."""

    gpu_index = current_gpu_index()
    if gpu_index is None:
        return _original_concat(
            clip_files,
            output_file,
            threads,
            output_dir,
            max_duration,
        )

    concat_list_file = str(Path(output_dir) / "ffmpeg-concat-list.txt")
    with open(concat_list_file, "w", encoding="utf-8") as fp:
        for clip_file in clip_files:
            formatted = video_service._format_ffmpeg_concat_path(clip_file)
            fp.write(f"file '{formatted}'\n")

    def build_command(codec: str) -> list[str]:
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
            codec,
        ]
        command.extend(_gpu_ffmpeg_params(codec))
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
        return command

    def run_concat(codec: str):
        result = subprocess.run(
            build_command(codec),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(detail or "ffmpeg concat failed")
        return codec

    try:
        effective_codec = video_service._get_effective_video_codec()
        try:
            return run_concat(effective_codec)
        except Exception as exc:
            if effective_codec == video_service._DEFAULT_VIDEO_CODEC:
                raise
            result_codec = run_concat(video_service._DEFAULT_VIDEO_CODEC)
            video_service._disable_runtime_video_codec(effective_codec, str(exc))
            return result_codec
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
