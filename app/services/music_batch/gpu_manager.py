from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from typing import Callable

from loguru import logger

from app.services.music_batch.gpu import (
    build_gpu_assignments,
    current_gpu_index,
    detect_nvidia_gpu_indices,
    install_video_gpu_hooks,
    is_nvenc_encoder,
    nvenc_gpu_context,
)
from app.services.music_batch.input import sort_song_items
from app.services.music_batch.manager import MusicBatchManager as BaseMusicBatchManager
from app.services.music_batch.models import (
    BatchState,
    BatchStatus,
    SongItem,
    SongStatus,
)
from app.services.music_batch.resource_guard import ResourceGuard
from app.services.music_batch.state import BatchStateStore

GpuDetector = Callable[[], list[int]]


class MusicBatchManager(BaseMusicBatchManager):
    """Music Batch manager with automatic GPU scheduling and Cloud Safe admission."""

    def __init__(
        self,
        render_adapter=None,
        song_renderer=None,
        gpu_detector: GpuDetector | None = None,
        resource_guard: ResourceGuard | None = None,
    ) -> None:
        self.gpu_detector = gpu_detector or detect_nvidia_gpu_indices
        self.resource_guard = resource_guard or ResourceGuard.from_env()
        install_video_gpu_hooks()

        wrapped_renderer = None
        if song_renderer is not None:
            def wrapped_renderer(song, resolved, output_path):
                effective = dict(resolved)
                effective["gpu_index"] = current_gpu_index()
                return song_renderer(song, effective, output_path)

        super().__init__(
            render_adapter=render_adapter,
            song_renderer=wrapped_renderer,
        )

    def _build_render_params(
        self, song: SongItem, resolved: dict[str, object]
    ) -> dict[str, object]:
        params = super()._build_render_params(song, resolved)
        params["gpu_index"] = current_gpu_index()
        return params

    def _detect_gpus_for_encoder(self, encoder: str) -> list[int]:
        if not is_nvenc_encoder(encoder):
            return []
        try:
            return self.gpu_detector()
        except Exception as exc:
            logger.warning(f"failed to detect NVIDIA GPUs for Music Batch: {exc}")
            return []

    def _process_song_on_gpu(
        self,
        store: BatchStateStore,
        added_index: int,
        gpu_index: int | None,
    ) -> None:
        current = store.load()
        self.resource_guard.wait_until_safe(
            Path(current.settings.output_root),
            gpu_index,
        )

        def record_assignment(state: BatchState) -> BatchState:
            song = self._song_by_added_index(state, added_index)
            song.gpu_index = gpu_index
            return state

        store.mutate(record_assignment)
        if gpu_index is not None:
            logger.info(
                f"Music Batch song index {added_index} assigned to NVIDIA GPU {gpu_index}"
            )
        with nvenc_gpu_context(gpu_index):
            super()._process_song(store, added_index)

    def run_batch(self, state: BatchState) -> BatchState:
        batch_dir = Path(state.batch_dir)
        batch_dir.mkdir(parents=True, exist_ok=True)
        store = BatchStateStore(batch_dir)
        if not store.state_path.exists():
            store.save(state)

        def start(current: BatchState) -> BatchState:
            current.status = BatchStatus.processing
            current.fatal_error = None
            if current.started_at is None:
                from app.services.music_batch.manager import _utc_now

                current.started_at = _utc_now()
            current.completed_at = None
            return current

        running = store.mutate(start)
        self.used_clips.load_snapshot(running.used_clips)
        ordered = sort_song_items(running.songs, running.settings.sort_mode)
        pending_indices = [
            song.added_index
            for song in ordered
            if song.status
            in {
                SongStatus.pending,
                SongStatus.retrying,
                SongStatus.processing,
            }
        ]

        gpu_indices = self._detect_gpus_for_encoder(running.settings.video_encoder)
        gpu_assignments = build_gpu_assignments(
            pending_indices,
            running.settings.video_encoder,
            gpu_indices,
        )
        if is_nvenc_encoder(running.settings.video_encoder):
            if len(gpu_indices) >= 2:
                logger.info(
                    "Music Batch multi-GPU scheduling enabled: "
                    f"GPUs={gpu_indices}, parallel_jobs={running.settings.parallel_jobs}"
                )
            elif len(gpu_indices) == 1:
                logger.info(
                    "Music Batch detected one NVIDIA GPU; all NVENC jobs use "
                    f"GPU {gpu_indices[0]}"
                )
            else:
                logger.warning(
                    "Music Batch could not enumerate NVIDIA GPUs; NVENC preflight "
                    "will determine whether the selected encoder is usable"
                )

        codec_context = (
            self._runtime_codec_context(running.settings.video_encoder)
            if self._uses_default_core
            else nullcontext()
        )
        try:
            with codec_context:
                if running.settings.parallel_jobs <= 1:
                    for added_index in pending_indices:
                        self._process_song_on_gpu(
                            store,
                            added_index,
                            gpu_assignments.get(added_index),
                        )
                else:
                    with ThreadPoolExecutor(
                        max_workers=running.settings.parallel_jobs,
                        thread_name_prefix="music-batch",
                    ) as executor:
                        futures = {
                            executor.submit(
                                self._process_song_on_gpu,
                                store,
                                added_index,
                                gpu_assignments.get(added_index),
                            ): added_index
                            for added_index in pending_indices
                        }
                        for future in as_completed(futures):
                            future.result()

            finalized = self._finalize_status(store)
            finalized = self._combine_if_requested(store)
            self._write_reports(finalized)
            return finalized
        except Exception as exc:
            self._mark_fatal(store, exc)
            raise
