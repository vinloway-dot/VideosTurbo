from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4

import streamlit as st

from app.config import config
from app.services.music_batch.input import (
    SUPPORTED_AUDIO_EXTENSIONS,
    discover_audio_files,
    normalize_uploaded_paths,
)
from app.services.music_batch.manager import MusicBatchManager
from app.services.music_batch.models import (
    BatchSettings,
    BatchStatus,
    SongItem,
    SongOverride,
    SongStatus,
    SortMode,
)
from app.services.music_batch.preflight import run_preflight
from app.services.music_batch.state import BatchStateStore

_ENCODERS = [
    "libx264",
    "h264_nvenc",
    "h264_amf",
    "h264_qsv",
    "h264_mf",
    "h264_videotoolbox",
]
_ASPECTS = ["16:9", "9:16", "1:1"]
_TRANSITIONS = [
    None,
    "Shuffle",
    "FadeIn",
    "FadeOut",
    "SlideIn",
    "SlideOut",
    "ZoomIn",
    "ZoomOut",
]
_SOURCES = ["pexels", "pixabay", "coverr"]
_OVERRIDE_SUFFIXES = (
    "enabled",
    "script",
    "keywords",
    "sources",
    "clip_duration",
    "concat",
    "transition",
    "speed",
)


def _parse_keywords(value: str) -> list[str]:
    normalized = value.replace("\n", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _stage_uploaded_files(uploaded_files, output_root: Path) -> list[Path]:
    if not uploaded_files:
        return []
    staging = output_root / ".music_batch_uploads" / str(uuid4())
    staging.mkdir(parents=True, exist_ok=False)
    result: list[Path] = []
    for index, uploaded in enumerate(uploaded_files):
        original = Path(uploaded.name).name
        suffix = Path(original).suffix.lower()
        if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
            continue
        target = staging / original
        if target.exists():
            target = staging / f"{Path(original).stem}_{index + 1}{suffix}"
        target.write_bytes(uploaded.getbuffer())
        result.append(target.resolve())
    return result


def _find_incomplete_batches(output_root: Path) -> list[Path]:
    if not output_root.is_dir():
        return []
    results: list[Path] = []
    for state_file in output_root.glob("*/batch_state.json"):
        try:
            state = BatchStateStore(state_file.parent).load()
        except Exception:
            continue
        if state.status not in {
            BatchStatus.completed,
            BatchStatus.completed_with_failures,
            BatchStatus.failed,
        }:
            results.append(state_file.parent)
    return sorted(results, key=lambda path: path.stat().st_mtime, reverse=True)


def _thread_alive() -> bool:
    thread = st.session_state.get("music_batch_thread")
    return bool(thread and thread.is_alive())


def _start_thread(target, *, name: str, error_dir: Path | None = None) -> None:
    if _thread_alive():
        st.warning("A Music Batch job is already running in this WebUI process.")
        return

    def runner():
        try:
            target()
        except Exception as exc:
            if error_dir is not None:
                try:
                    (error_dir / "batch_fatal_error.txt").write_text(
                        f"{type(exc).__name__}: {exc}\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass

    thread = threading.Thread(target=runner, name=name, daemon=True)
    st.session_state["music_batch_thread"] = thread
    thread.start()


def _clear_override(prefix: str) -> None:
    for suffix in _OVERRIDE_SUFFIXES:
        st.session_state.pop(f"{prefix}_{suffix}", None)


def _render_override(prefix: str) -> SongOverride | None:
    top = st.columns([3, 1])
    enabled = top[0].checkbox(
        "Override global settings",
        key=f"{prefix}_enabled",
    )
    if top[1].button("Reset to Global", key=f"{prefix}_reset"):
        _clear_override(prefix)
        st.rerun()
    if not enabled:
        return None

    script = st.text_area("Video Script override", key=f"{prefix}_script")
    keywords_text = st.text_area(
        "Video Keywords override", key=f"{prefix}_keywords"
    )
    sources = st.multiselect(
        "Stock Sources override",
        _SOURCES,
        key=f"{prefix}_sources",
    )
    clip_duration = st.number_input(
        "Clip Duration override (seconds)",
        min_value=1,
        max_value=60,
        value=8,
        key=f"{prefix}_clip_duration",
    )
    concat_mode = st.selectbox(
        "Concat Mode override",
        ["random", "sequential"],
        key=f"{prefix}_concat",
    )
    transition = st.selectbox(
        "Transition override",
        _TRANSITIONS,
        format_func=lambda value: "None" if value is None else value,
        key=f"{prefix}_transition",
    )
    speed = st.number_input(
        "Clip Speed override",
        min_value=0.1,
        max_value=4.0,
        value=1.0,
        step=0.1,
        key=f"{prefix}_speed",
    )
    return SongOverride(
        video_script=script or None,
        video_keywords=_parse_keywords(keywords_text) or None,
        stock_sources=list(sources) or None,
        video_clip_duration=int(clip_duration),
        video_concat_mode=concat_mode,
        video_transition_mode=transition,
        video_clip_speed=float(speed),
    )


def _render_state(batch_dir: Path) -> None:
    state_file = batch_dir / "batch_state.json"
    if not state_file.is_file():
        return
    try:
        state = BatchStateStore(batch_dir).load()
    except Exception as exc:
        st.error(f"Cannot read batch state: {exc}")
        return

    completed = sum(song.status == SongStatus.completed for song in state.songs)
    failed = sum(song.status == SongStatus.failed for song in state.songs)
    skipped = sum(song.status == SongStatus.skipped for song in state.songs)
    total = len(state.songs)
    finished = completed + failed + skipped
    st.progress(
        finished / total if total else 0.0,
        text=f"{completed} completed · {failed} failed · {total} total",
    )
    st.write(
        f"**Batch:** `{state.batch_id}`  ·  **Status:** `{state.status.value}`"
    )
    if state.fatal_error:
        st.error(state.fatal_error)

    st.dataframe(
        [
            {
                "Song": Path(song.source_path).name,
                "Status": song.status.value,
                "Attempts": song.attempts,
                "Output": song.output_path or "",
                "Error": song.latest_error or "",
            }
            for song in state.songs
        ],
        use_container_width=True,
        hide_index=True,
    )

    controls = st.columns(4)
    if controls[0].button("Refresh Status", key=f"refresh_{state.batch_id}"):
        st.rerun()

    if failed and not _thread_alive():
        if controls[1].button(
            "Retry Failed", key=f"retry_failed_{state.batch_id}"
        ):
            manager = MusicBatchManager()
            st.session_state["music_batch_active_dir"] = str(batch_dir)
            _start_thread(
                lambda: manager.retry_failed(batch_dir),
                name=f"music-batch-retry-{state.batch_id}",
                error_dir=batch_dir,
            )
            st.rerun()

    if (
        state.status == BatchStatus.needs_reencode_confirmation
        and not _thread_alive()
    ):
        st.warning(
            "The completed videos cannot be joined with stream copy. A full "
            "re-encode may take a long time."
        )
        decision = st.columns(2)
        if decision[0].button(
            "Re-encode and Combine",
            key=f"approve_reencode_{state.batch_id}",
            type="primary",
        ):
            manager = MusicBatchManager()
            _start_thread(
                lambda: manager.approve_reencode(batch_dir),
                name=f"music-batch-reencode-{state.batch_id}",
                error_dir=batch_dir,
            )
            st.rerun()
        if decision[1].button(
            "Keep Separate Videos",
            key=f"keep_separate_{state.batch_id}",
        ):
            MusicBatchManager().keep_separate(batch_dir)
            st.rerun()

    report = batch_dir / "batch_report.txt"
    if report.is_file():
        with st.expander("Batch Report"):
            st.code(report.read_text(encoding="utf-8"), language="text")


def _render_resume_section(default_root: str) -> None:
    with st.expander("Resume an incomplete batch", expanded=False):
        root_text = st.text_input(
            "Search under Output Folder",
            value=default_root,
            key="music_batch_resume_root",
        )
        if not root_text.strip():
            return
        root = Path(root_text).expanduser()
        incomplete = _find_incomplete_batches(root)
        if not incomplete:
            st.info("No incomplete batches were found in this folder.")
            return
        selected = st.selectbox(
            "Incomplete Batch",
            incomplete,
            format_func=lambda path: path.name,
        )
        buttons = st.columns(2)
        if buttons[0].button(
            "Resume", type="primary", disabled=_thread_alive()
        ):
            manager = MusicBatchManager()
            st.session_state["music_batch_active_dir"] = str(selected)
            _start_thread(
                lambda: manager.resume_batch(selected),
                name=f"music-batch-resume-{selected.name}",
                error_dir=selected,
            )
            st.rerun()
        if buttons[1].button("Start Over", disabled=_thread_alive()):
            manager = MusicBatchManager()
            restarted = manager.start_over(selected)
            new_dir = Path(restarted.batch_dir)
            st.session_state["music_batch_active_dir"] = str(new_dir)
            _start_thread(
                lambda: manager.run_batch(restarted),
                name=f"music-batch-restart-{new_dir.name}",
                error_dir=new_dir,
            )
            st.rerun()


def render_music_batch_page() -> None:
    st.title("🎵 Music Batch")
    st.caption(
        "Create one stock-footage video per song with the existing VideosTurbo "
        "rendering core, then optionally combine the completed videos."
    )

    _render_resume_section(st.session_state.get("music_batch_output_root", ""))

    st.subheader("1. Input / Output")
    uploaded_files = st.file_uploader(
        "Upload Multiple Files",
        type=["mp3", "wav", "m4a", "flac"],
        accept_multiple_files=True,
        key="music_batch_uploads",
    )
    folder_path_text = st.text_input(
        "Folder Path",
        placeholder=r"D:\Music\Album01",
        key="music_batch_folder_path",
    )
    include_subfolders = st.checkbox(
        "Include subfolders",
        value=False,
        key="music_batch_include_subfolders",
    )
    folder_paths: list[Path] = []
    if folder_path_text.strip():
        folder_paths = discover_audio_files(
            Path(folder_path_text).expanduser(), include_subfolders
        )
        st.caption(f"Found {len(folder_paths)} supported audio file(s).")

    output_root_text = st.text_input(
        "Output Folder",
        placeholder=r"D:\MyVideos",
        key="music_batch_output_root",
    )

    st.subheader("2. Global Settings")
    global_script = st.text_area(
        "Video Script",
        value=(
            "Peaceful relaxing nature scenery with oceans, forests, mountains, "
            "waterfalls, sunsets and clouds."
        ),
        key="music_batch_script",
    )
    global_keywords_text = st.text_area(
        "Video Keywords (English)",
        value=(
            "peaceful ocean, ocean waves, tropical beach, sunset beach, misty "
            "forest, green forest, mountain landscape, peaceful lake, nature "
            "waterfall, beautiful clouds"
        ),
        key="music_batch_keywords",
    )
    global_sources = st.multiselect(
        "Stock Video Sources",
        _SOURCES,
        default=["pexels"],
        key="music_batch_sources",
    )

    row1 = st.columns(4)
    aspect = row1[0].selectbox(
        "Aspect Ratio / Resolution",
        _ASPECTS,
        index=0,
        help="16:9 = 1920x1080, 9:16 = 1080x1920, 1:1 = 1080x1080",
    )
    clip_duration = row1[1].number_input(
        "Clip Duration (seconds)", min_value=1, max_value=60, value=8
    )
    concat_mode = row1[2].selectbox(
        "Concat Mode", ["random", "sequential"], index=0
    )
    transition = row1[3].selectbox(
        "Transition",
        _TRANSITIONS,
        index=0,
        format_func=lambda value: "None" if value is None else value,
    )

    saved_encoder = str(config.app.get("video_codec", "libx264") or "libx264")
    if saved_encoder not in _ENCODERS:
        saved_encoder = "libx264"
    row2 = st.columns(4)
    clip_speed = row2[0].number_input(
        "Clip Speed", min_value=0.1, max_value=4.0, value=1.0, step=0.1
    )
    encoder = row2[1].selectbox(
        "Video Encoder",
        _ENCODERS,
        index=_ENCODERS.index(saved_encoder),
    )
    retry_count = row2[2].number_input(
        "Retry Count", min_value=0, max_value=10, value=2, step=1
    )
    parallel_jobs = row2[3].number_input(
        "Parallel Jobs", min_value=1, max_value=4, value=1, step=1
    )
    if parallel_jobs > 1:
        st.warning(
            "Parallel Jobs above 1 can significantly increase CPU, RAM, GPU, "
            "VRAM, disk, and network usage."
        )

    row3 = st.columns(3)
    sort_mode_value = row3[0].selectbox(
        "Sort",
        [SortMode.filename.value, SortMode.added.value],
        format_func=lambda value: (
            "Filename Order" if value == "filename" else "Added Order"
        ),
    )
    avoid_reuse = row3[1].checkbox(
        "Avoid reusing clips in this batch", value=False
    )
    combine_all = row3[2].checkbox(
        "Combine all videos after batch", value=False
    )

    preview_names = [uploaded.name for uploaded in uploaded_files or []] + [
        path.name for path in folder_paths
    ]
    overrides: list[SongOverride | None] = []
    if preview_names:
        st.subheader("3. Songs and Optional Overrides")
        st.caption(f"{len(preview_names)} song(s) selected")
        for index, name in enumerate(preview_names):
            with st.expander(f"{index + 1:03d} · {name}"):
                overrides.append(
                    _render_override(f"music_batch_override_{index}")
                )

    if st.button(
        "Generate All",
        type="primary",
        use_container_width=True,
        disabled=_thread_alive(),
    ):
        if not output_root_text.strip():
            st.error("Choose an Output Folder before starting the batch.")
            st.stop()
        if not global_sources:
            st.error("Select at least one Stock Video Source.")
            st.stop()

        output_root = Path(output_root_text).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        staged_uploads = _stage_uploaded_files(uploaded_files, output_root)
        all_paths = normalize_uploaded_paths([*staged_uploads, *folder_paths])
        if not all_paths:
            st.error("Add at least one supported audio file before starting the batch.")
            st.stop()

        songs = [
            SongItem(
                source_path=str(source_path),
                added_index=index,
                override=overrides[index] if index < len(overrides) else None,
            )
            for index, source_path in enumerate(all_paths)
        ]
        settings = BatchSettings(
            output_root=str(output_root),
            video_script=global_script,
            video_keywords=_parse_keywords(global_keywords_text),
            stock_sources=list(global_sources),
            video_aspect=aspect,
            video_concat_mode=concat_mode,
            video_transition_mode=transition,
            video_clip_duration=int(clip_duration),
            video_clip_speed=float(clip_speed),
            video_encoder=encoder,
            retry_count=int(retry_count),
            parallel_jobs=int(parallel_jobs),
            sort_mode=SortMode(sort_mode_value),
            avoid_reusing_clips=avoid_reuse,
            combine_all=combine_all,
        )
        issues = run_preflight(settings, songs)
        for issue in issues:
            (st.error if issue.level == "error" else st.warning)(issue.message)
        if any(issue.level == "error" for issue in issues):
            st.stop()

        manager = MusicBatchManager()
        state = manager.create_batch(settings, songs)
        batch_dir = Path(state.batch_dir)
        st.session_state["music_batch_active_dir"] = str(batch_dir)
        _start_thread(
            lambda: manager.run_batch(state),
            name=f"music-batch-{state.batch_id}",
            error_dir=batch_dir,
        )
        st.success(f"Batch started: {batch_dir}")
        st.rerun()

    active_dir = st.session_state.get("music_batch_active_dir")
    if active_dir:
        st.divider()
        st.subheader("Current / Last Batch")
        _render_state(Path(active_dir))
