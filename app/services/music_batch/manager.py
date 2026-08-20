from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from app.config import config
from app.models.schema import VideoParams
from app.services import task as task_service
from app.services.music_batch.concat import (
    are_stream_copy_compatible,
    concat_reencode,
    concat_stream_copy,
)
from app.services.music_batch.input import allocate_output_path, sort_song_items
from app.services.music_batch.models import (
    BatchSettings,
    BatchState,
    BatchStatus,
    SongItem,
    SongStatus,
    resolve_song_settings,
)
from app.services.music_batch.sources import UsedClipRegistry, build_source_plan
from app.services.music_batch.state import BatchStateStore, make_restart_directory

RenderAdapter = Callable[[dict[str, object], Path], Path]
SongRenderer = Callable[[SongItem, dict[str, object], Path], Path]


class BatchFatalError(RuntimeError):
    """A batch-level failure where continuing would be unsafe or misleading."""


class EncoderFallbackError(BatchFatalError):
    """The requested hardware encoder failed and the existing core fell back."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MusicBatchManager:
    def __init__(
        self,
        render_adapter: RenderAdapter | None = None,
        song_renderer: SongRenderer | None = None,
    ) -> None:
        self.render_adapter = render_adapter
        self.song_renderer = song_renderer
        self.used_clips = UsedClipRegistry()

    @property
    def _uses_default_core(self) -> bool:
        return self.song_renderer is None and self.render_adapter is None

    def create_batch(
        self,
        settings: BatchSettings,
        songs: list[SongItem],
        *,
        batch_id: str | None = None,
    ) -> BatchState:
        root = Path(settings.output_root)
        root.mkdir(parents=True, exist_ok=True)
        if batch_id is None:
            base = datetime.now().strftime("batch_%Y-%m-%d_%H%M%S")
            candidate = root / base
            suffix = 1
            while candidate.exists():
                candidate = root / f"{base}_{suffix:02d}"
                suffix += 1
        else:
            candidate = root / batch_id
            if candidate.exists():
                suffix = 1
                while (root / f"{batch_id}_{suffix:02d}").exists():
                    suffix += 1
                candidate = root / f"{batch_id}_{suffix:02d}"

        candidate.mkdir(parents=True, exist_ok=False)
        ordered = sort_song_items(songs, settings.sort_mode)
        state = BatchState(
            batch_id=candidate.name,
            batch_dir=str(candidate),
            settings=settings,
            songs=ordered,
        )
        BatchStateStore(candidate).save(state)
        return state

    def start_over(self, batch_dir: Path) -> BatchState:
        previous = BatchStateStore(batch_dir).load()
        restart_dir = make_restart_directory(Path(batch_dir))
        restart_dir.mkdir(parents=True, exist_ok=False)
        songs = [
            song.model_copy(
                update={
                    "status": SongStatus.pending,
                    "attempts": 0,
                    "progress": 0,
                    "output_path": None,
                    "latest_error": None,
                    "started_at": None,
                    "completed_at": None,
                }
            )
            for song in previous.songs
        ]
        restarted = BatchState(
            batch_id=restart_dir.name,
            batch_dir=str(restart_dir),
            settings=previous.settings,
            songs=songs,
        )
        BatchStateStore(restart_dir).save(restarted)
        return restarted

    def _build_render_params(
        self, song: SongItem, resolved: dict[str, object]
    ) -> dict[str, object]:
        sources = list(resolved.get("stock_sources") or ["pexels"])
        params = {
            "video_subject": Path(song.source_path).stem,
            "video_script": str(resolved.get("video_script") or ""),
            "video_terms": list(resolved.get("video_keywords") or []),
            "video_aspect": str(resolved.get("video_aspect") or "16:9"),
            "video_concat_mode": str(resolved.get("video_concat_mode") or "random"),
            "video_transition_mode": resolved.get("video_transition_mode"),
            "video_clip_duration": int(resolved.get("video_clip_duration") or 8),
            "video_clip_speed": float(resolved.get("video_clip_speed") or 1.0),
            "video_count": 1,
            "video_source": sources[0] if sources else "pexels",
            "custom_audio_file": str(Path(song.source_path).resolve()),
            "voice_volume": 1.0,
            "bgm_type": "",
            "bgm_file": "",
            "bgm_volume": 0.0,
            "subtitle_enabled": False,
            "video_encoder": str(resolved.get("video_encoder") or "libx264"),
            "stock_sources": sources,
            "avoid_reusing_clips": bool(resolved.get("avoid_reusing_clips", False)),
        }
        progress_callback = resolved.get("_progress_callback")
        if callable(progress_callback):
            params["_progress_callback"] = progress_callback
        return params

    @staticmethod
    def _notify_progress(render_params: dict[str, object], progress: int) -> None:
        callback = render_params.get("_progress_callback")
        if callable(callback):
            callback(max(0, min(100, int(progress))))

    def render_song(
        self, song: SongItem, resolved: dict[str, object], output_path: Path
    ) -> Path:
        params = self._build_render_params(song, resolved)
        if self.render_adapter is not None:
            return Path(self.render_adapter(params, Path(output_path)))
        return self._render_with_existing_core(song, params, Path(output_path))

    def _render_with_existing_core(
        self, song: SongItem, render_params: dict[str, object], output_path: Path
    ) -> Path:
        task_id = f"music-batch-{uuid4()}"
        model_fields = VideoParams.model_fields
        params = VideoParams(
            **{key: value for key, value in render_params.items() if key in model_fields}
        )
        task_service.sm.state.update_task(
            task_id,
            state=task_service.const.TASK_STATE_PROCESSING,
            progress=5,
        )
        self._notify_progress(render_params, 8)

        script = params.video_script or task_service.generate_script(task_id, params)
        if not script:
            raise RuntimeError("video script generation failed")
        self._notify_progress(render_params, 12)

        terms = task_service.generate_terms(task_id, params, script)
        if not terms:
            raise RuntimeError("video keyword generation failed")
        task_service.save_script_data(task_id, script, terms, params)
        self._notify_progress(render_params, 16)

        audio_file, audio_duration, _sub_maker = task_service.generate_audio(
            task_id, params, script
        )
        if not audio_file or not audio_duration:
            task = task_service.sm.state.get_task(task_id) or {}
            raise RuntimeError(str(task.get("error") or "custom audio processing failed"))
        self._notify_progress(render_params, 22)

        sources = list(render_params.get("stock_sources") or [params.video_source])
        source_plans = build_source_plan(sources, list(terms), float(audio_duration))
        downloaded: list[str] = []
        provider_errors: list[str] = []
        avoid_reuse = bool(render_params.get("avoid_reusing_clips", False))
        plan_count = max(1, len(source_plans))
        for plan_index, source_plan in enumerate(source_plans, start=1):
            provider_params = params.model_copy(
                update={"video_source": source_plan.provider}
            )
            materials = task_service.get_video_materials(
                task_id,
                provider_params,
                terms,
                source_plan.requested_duration,
            )
            if not materials:
                provider_errors.append(source_plan.provider)
            else:
                candidates = [
                    (Path(material_path).name, material_path)
                    for material_path in materials
                ]
                selected = self.used_clips.filter_candidates(
                    source_plan.provider,
                    candidates,
                    avoid_reuse=avoid_reuse,
                )
                for clip_id, material_path in selected:
                    downloaded.append(str(material_path))
                    if avoid_reuse:
                        self.used_clips.mark(source_plan.provider, clip_id)
            self._notify_progress(
                render_params,
                22 + round((plan_index / plan_count) * 28),
            )

        if not downloaded:
            providers = ", ".join(provider_errors or sources)
            raise RuntimeError(f"no usable stock videos were downloaded from: {providers}")

        self._notify_progress(render_params, 55)
        final_paths, _combined_paths, warnings = task_service.generate_final_videos(
            task_id=task_id,
            params=params,
            downloaded_videos=downloaded,
            audio_file=audio_file,
            subtitle_path="",
            audio_duration=audio_duration,
        )
        self._notify_progress(render_params, 95)

        requested_codec = str(render_params.get("video_encoder") or "libx264")
        disabled_codecs = getattr(
            task_service.video,
            "_runtime_disabled_video_codecs",
            set(),
        )
        if requested_codec != "libx264" and requested_codec in disabled_codecs:
            raise EncoderFallbackError(
                f"{requested_codec} failed at runtime and the existing renderer fell "
                "back to libx264. Music Batch stopped so a long GPU batch is not "
                "silently converted to CPU rendering."
            )

        if not final_paths:
            raise RuntimeError("existing video core produced no final video")
        source_output = Path(final_paths[0])
        if not source_output.is_file():
            raise RuntimeError(f"existing video core output is missing: {source_output}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_output, output_path)
        task_service.sm.state.update_task(
            task_id,
            state=task_service.const.TASK_STATE_COMPLETE,
            progress=100,
            videos=final_paths,
            warnings=warnings or None,
        )
        return output_path

    @contextmanager
    def _runtime_codec_context(self, codec: str) -> Iterator[None]:
        with config.runtime_config_lock():
            sentinel = object()
            previous = config.app.get("video_codec", sentinel)
            config.app["video_codec"] = codec
            try:
                yield
            finally:
                if previous is sentinel:
                    config.app.pop("video_codec", None)
                else:
                    config.app["video_codec"] = previous

    @staticmethod
    def _song_by_added_index(state: BatchState, added_index: int) -> SongItem:
        for song in state.songs:
            if song.added_index == added_index:
                return song
        raise KeyError(f"song with added_index={added_index} not found")

    def _set_song_progress(
        self, store: BatchStateStore, added_index: int, progress: int
    ) -> None:
        normalized = max(0, min(100, int(progress)))

        def update(current: BatchState) -> BatchState:
            target = self._song_by_added_index(current, added_index)
            target.progress = normalized
            return current

        store.mutate(update)

    def _process_song(self, store: BatchStateStore, added_index: int) -> None:
        initial = store.load()
        settings = initial.settings
        retry_limit = settings.retry_count

        while True:
            def begin_attempt(state: BatchState) -> BatchState:
                song = self._song_by_added_index(state, added_index)
                if song.status == SongStatus.completed:
                    return state
                song.attempts += 1
                song.status = (
                    SongStatus.processing if song.attempts == 1 else SongStatus.retrying
                )
                song.progress = 5
                song.started_at = _utc_now()
                song.latest_error = None
                if not song.output_path:
                    song.output_path = str(
                        allocate_output_path(Path(state.batch_dir), Path(song.source_path))
                    )
                return state

            state = store.mutate(begin_attempt)
            song = self._song_by_added_index(state, added_index)
            if song.status == SongStatus.completed:
                return

            final_path = Path(song.output_path or "")
            temp_path = final_path.with_name(
                f"{final_path.stem}.rendering{final_path.suffix}"
            )
            temp_path.unlink(missing_ok=True)
            resolved = resolve_song_settings(settings, song)
            resolved["_progress_callback"] = lambda value: self._set_song_progress(
                store, added_index, value
            )

            try:
                renderer = self.song_renderer or self.render_song
                rendered = Path(renderer(song, resolved, temp_path))
                if rendered != temp_path:
                    if not rendered.is_file():
                        raise RuntimeError(
                            f"renderer reported success but output is missing: {rendered}"
                        )
                    shutil.copy2(rendered, temp_path)
                if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                    raise RuntimeError("renderer produced an empty or missing video")
                os.replace(temp_path, final_path)

                def complete(current: BatchState) -> BatchState:
                    target = self._song_by_added_index(current, added_index)
                    target.status = SongStatus.completed
                    target.progress = 100
                    target.output_path = str(final_path)
                    target.completed_at = _utc_now()
                    target.latest_error = None
                    if current.settings.avoid_reusing_clips:
                        current.used_clips = self.used_clips.snapshot()
                    return current

                store.mutate(complete)
                return
            except BatchFatalError:
                temp_path.unlink(missing_ok=True)
                raise
            except Exception as exc:
                temp_path.unlink(missing_ok=True)
                error = f"{type(exc).__name__}: {exc}"

                def fail_or_retry(current: BatchState) -> BatchState:
                    target = self._song_by_added_index(current, added_index)
                    target.latest_error = error
                    if target.attempts <= retry_limit:
                        target.status = SongStatus.retrying
                    else:
                        target.status = SongStatus.failed
                        target.progress = 100
                        target.completed_at = _utc_now()
                    return current

                updated = store.mutate(fail_or_retry)
                target = self._song_by_added_index(updated, added_index)
                if target.status == SongStatus.failed:
                    return

    def _ordered_completed_paths(self, state: BatchState) -> list[Path]:
        ordered = sort_song_items(state.songs, state.settings.sort_mode)
        return [
            Path(song.output_path)
            for song in ordered
            if song.status == SongStatus.completed
            and song.output_path
            and Path(song.output_path).is_file()
        ]

    def _finalize_status(self, store: BatchStateStore) -> BatchState:
        def finalize(state: BatchState) -> BatchState:
            failed = any(song.status == SongStatus.failed for song in state.songs)
            state.status = (
                BatchStatus.completed_with_failures if failed else BatchStatus.completed
            )
            state.completed_at = _utc_now()
            state.fatal_error = None
            return state

        return store.mutate(finalize)

    def _combine_if_requested(self, store: BatchStateStore) -> BatchState:
        state = store.load()
        if not state.settings.combine_all:
            return state
        paths = self._ordered_completed_paths(state)
        if not paths:
            return state

        output = Path(state.batch_dir) / "Full_Compilation.mp4"
        compatible, reason = are_stream_copy_compatible(paths)
        if not compatible:
            def require_confirmation(current: BatchState) -> BatchState:
                current.status = BatchStatus.needs_reencode_confirmation
                current.compilation_status = "needs_reencode_confirmation"
                current.compilation_error = reason
                current.compilation_members = [str(path) for path in paths]
                return current

            return store.mutate(require_confirmation)

        concat_stream_copy(paths, output)

        def mark_complete(current: BatchState) -> BatchState:
            current.compilation_status = "completed"
            current.compilation_path = str(output)
            current.compilation_members = [str(path) for path in paths]
            current.compilation_error = None
            return current

        return store.mutate(mark_complete)

    def _mark_fatal(
        self, store: BatchStateStore, error: BaseException
    ) -> BatchState | None:
        message = f"{type(error).__name__}: {error}"
        try:
            def fail(current: BatchState) -> BatchState:
                current.status = BatchStatus.failed
                current.fatal_error = message
                current.completed_at = _utc_now()
                for song in current.songs:
                    if song.status in {SongStatus.processing, SongStatus.retrying}:
                        song.status = SongStatus.pending
                        song.progress = 0
                return current

            failed = store.mutate(fail)
            self._write_reports(failed)
            return failed
        except Exception:
            return None

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
                current.started_at = _utc_now()
            current.completed_at = None
            return current

        running = store.mutate(start)
        self.used_clips.load_snapshot(running.used_clips)
        ordered = sort_song_items(running.songs, running.settings.sort_mode)
        pending_indices = [
            song.added_index
            for song in ordered
            if song.status in {
                SongStatus.pending,
                SongStatus.retrying,
                SongStatus.processing,
            }
        ]

        codec_context = (
            self._runtime_codec_context(running.settings.video_encoder)
            if self._uses_default_core
            else nullcontext()
        )
        try:
            with codec_context:
                if running.settings.parallel_jobs <= 1:
                    for added_index in pending_indices:
                        self._process_song(store, added_index)
                else:
                    with ThreadPoolExecutor(
                        max_workers=running.settings.parallel_jobs,
                        thread_name_prefix="music-batch",
                    ) as executor:
                        futures = {
                            executor.submit(
                                self._process_song, store, added_index
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

    def resume_batch(self, batch_dir: Path) -> BatchState:
        store = BatchStateStore(batch_dir)
        recovered = store.recover_interrupted()
        return self.run_batch(recovered)

    def retry_failed(self, batch_dir: Path) -> BatchState:
        store = BatchStateStore(batch_dir)
        recovered = store.retry_failed()
        return self.run_batch(recovered)

    def approve_reencode(self, batch_dir: Path) -> BatchState:
        store = BatchStateStore(batch_dir)
        state = store.load()
        if state.compilation_status != "needs_reencode_confirmation":
            raise ValueError("batch is not waiting for re-encode confirmation")
        paths = self._ordered_completed_paths(state)
        if not paths:
            raise ValueError("no completed videos are available to combine")
        output = Path(state.batch_dir) / "Full_Compilation.mp4"
        try:
            concat_reencode(paths, output, state.settings.video_encoder)
        except Exception as exc:
            self._mark_fatal(store, exc)
            raise

        def complete(current: BatchState) -> BatchState:
            current.compilation_status = "completed"
            current.compilation_path = str(output)
            current.compilation_members = [str(path) for path in paths]
            current.compilation_error = None
            failed = any(song.status == SongStatus.failed for song in current.songs)
            current.status = (
                BatchStatus.completed_with_failures if failed else BatchStatus.completed
            )
            current.completed_at = _utc_now()
            return current

        updated = store.mutate(complete)
        self._write_reports(updated)
        return updated

    def keep_separate(self, batch_dir: Path) -> BatchState:
        store = BatchStateStore(batch_dir)
        state = store.load()
        if state.compilation_status != "needs_reencode_confirmation":
            raise ValueError("batch is not waiting for a compilation decision")

        def keep(current: BatchState) -> BatchState:
            current.compilation_status = "kept_separate"
            current.compilation_path = None
            current.compilation_error = None
            failed = any(song.status == SongStatus.failed for song in current.songs)
            current.status = (
                BatchStatus.completed_with_failures if failed else BatchStatus.completed
            )
            current.completed_at = _utc_now()
            return current

        updated = store.mutate(keep)
        self._write_reports(updated)
        return updated

    def _write_reports(self, state: BatchState) -> None:
        batch_dir = Path(state.batch_dir)
        batch_dir.mkdir(parents=True, exist_ok=True)
        completed = [song for song in state.songs if song.status == SongStatus.completed]
        failed = [song for song in state.songs if song.status == SongStatus.failed]
        skipped = [song for song in state.songs if song.status == SongStatus.skipped]
        report = {
            "batch_id": state.batch_id,
            "status": state.status.value,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "completed_at": state.completed_at,
            "fatal_error": state.fatal_error,
            "settings": state.settings.model_dump(mode="json"),
            "sort_mode": state.settings.sort_mode.value,
            "total_songs": len(state.songs),
            "completed_count": len(completed),
            "failed_count": len(failed),
            "skipped_count": len(skipped),
            "songs": [song.model_dump(mode="json") for song in state.songs],
            "used_clips": state.used_clips,
            "compilation_status": state.compilation_status,
            "compilation_path": state.compilation_path,
            "compilation_members": state.compilation_members,
            "compilation_error": state.compilation_error,
        }
        (batch_dir / "batch_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        lines = [
            f"Batch: {state.batch_id}",
            f"Status: {state.status.value}",
            "",
            f"Songs: {len(state.songs)}",
            f"Completed: {len(completed)}",
            f"Failed: {len(failed)}",
            f"Skipped: {len(skipped)}",
        ]
        if state.fatal_error:
            lines.extend(["", f"Fatal error: {state.fatal_error}"])
        if failed:
            lines.extend(["", "Failed songs:"])
            for song in failed:
                lines.append(
                    f"- {Path(song.source_path).name}: "
                    f"{song.latest_error or 'unknown error'}"
                )
        if state.compilation_status:
            lines.extend(
                [
                    "",
                    f"Compilation: {state.compilation_status}",
                    f"Compilation output: {state.compilation_path or '-'}",
                    f"Compilation videos: {len(state.compilation_members)}",
                ]
            )
            if state.compilation_error:
                lines.append(f"Compilation error: {state.compilation_error}")
        (batch_dir / "batch_report.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )