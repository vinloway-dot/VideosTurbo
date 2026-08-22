from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import streamlit as st

from app.models.six_clip import SixClipPlan, SixClipSegment, empty_six_clip_plan
from app.services import six_clip_media
from app.services.six_clip_plan import build_master_prompt_batches
from app.utils import utils


PLAN_SESSION_KEY = "six_clip_plan"
MEDIA_SESSION_ID_KEY = "six_clip_media_session_id"


def _widget_key(index: int, field: str) -> str:
    return f"six_clip_{index}_{field}"


def get_session_plan(target_words: int = 130) -> SixClipPlan:
    raw = st.session_state.get(PLAN_SESSION_KEY)
    if isinstance(raw, SixClipPlan):
        plan = raw
    elif isinstance(raw, dict):
        try:
            plan = SixClipPlan.model_validate(raw)
        except Exception:
            plan = empty_six_clip_plan(target_words)
    else:
        plan = empty_six_clip_plan(target_words)
    if plan.target_words != int(target_words):
        plan.target_words = int(target_words)
    return plan


def set_session_plan(plan: SixClipPlan, *, sync_widgets: bool = True) -> None:
    st.session_state[PLAN_SESSION_KEY] = plan.model_dump(mode="json")
    if not sync_widgets:
        return
    for segment in plan.segments:
        st.session_state[_widget_key(segment.index, "title")] = segment.title
        st.session_state[_widget_key(segment.index, "narration")] = (
            segment.narration_context
        )
        st.session_state[_widget_key(segment.index, "prompt")] = segment.video_prompt


def _has_restorable_local_media(segment: SixClipSegment) -> bool:
    if segment.media_kind not in {"video", "image"} or not segment.media_path:
        return False
    try:
        media_path = Path(segment.media_path)
        return media_path.is_file() and media_path.stat().st_size > 0
    except (OSError, TypeError, ValueError):
        return False


def merge_media_for_unchanged_ranges(
    previous: SixClipPlan | None,
    rebuilt: SixClipPlan,
) -> SixClipPlan:
    """Keep media only when its absolute clip identity is unchanged."""
    if previous is None:
        return rebuilt

    previous_by_range = {
        (segment.index, segment.start_sec, segment.end_sec): segment
        for segment in previous.segments
        if _has_restorable_local_media(segment)
    }
    merged_segments = []
    for segment in rebuilt.segments:
        old_segment = previous_by_range.get(
            (segment.index, segment.start_sec, segment.end_sec)
        )
        if old_segment is None:
            merged_segments.append(segment)
        else:
            merged_segments.append(
                segment.model_copy(
                    update={
                        "media_kind": old_segment.media_kind,
                        "media_path": old_segment.media_path,
                    }
                )
            )
    return rebuilt.model_copy(update={"segments": merged_segments})


def timeline_page(
    plan: SixClipPlan,
    page: int,
    page_size: int = 6,
) -> tuple[list[SixClipSegment], int]:
    if page_size < 1:
        raise ValueError("page_size must be positive")
    page_count = max(1, (len(plan.segments) + page_size - 1) // page_size)
    selected_page = min(max(int(page), 1), page_count)
    start = (selected_page - 1) * page_size
    return list(plan.segments[start : start + page_size]), page_count


def _normalize_restored_plan(
    raw_plan,
    target_words: int | None = None,
) -> SixClipPlan | None:
    if not raw_plan:
        return None
    try:
        plan = (
            raw_plan
            if isinstance(raw_plan, SixClipPlan)
            else SixClipPlan.model_validate(raw_plan)
        )
    except Exception:
        return None

    normalized_segments: list[SixClipSegment] = []
    for segment in plan.segments:
        if _has_restorable_local_media(segment):
            normalized_segments.append(segment)
        else:
            # Task History may outlive imported/uploaded files. Preserve the user's
            # narration and prompt, but clear stale media so the UI visibly returns
            # to Missing and the fail-closed preflight cannot fall back to stock.
            normalized_segments.append(
                segment.model_copy(update={"media_kind": "", "media_path": ""})
            )

    restored_target_words = plan.target_words
    if target_words is not None:
        try:
            restored_target_words = int(target_words)
        except (TypeError, ValueError):
            restored_target_words = plan.target_words

    try:
        return SixClipPlan(
            target_words=restored_target_words,
            narration_duration_sec=plan.narration_duration_sec,
            timeline_duration_sec=plan.timeline_duration_sec,
            slot_duration_sec=plan.slot_duration_sec,
            narration_fingerprint=plan.narration_fingerprint,
            segments=normalized_segments,
        )
    except Exception:
        return None


def restore_plan_from_task_params(params) -> SixClipPlan | None:
    """Build a safe six-clip plan from persisted task parameters.

    Task manifests contain only local media references. A reference is restored
    only while that local file still exists and is non-empty; missing files are
    deliberately cleared instead of being replaced by stock material.
    """
    if not isinstance(params, dict):
        return None
    return _normalize_restored_plan(
        params.get("six_clip_plan"),
        target_words=params.get("target_words"),
    )


def restore_session_plan(raw_plan, target_words: int | None = None) -> bool:
    """Restore a persisted task plan without silently replacing missing media."""
    plan = _normalize_restored_plan(raw_plan, target_words=target_words)
    if plan is None:
        return False
    set_session_plan(plan, sync_widgets=True)
    return True


def _media_session_dir() -> Path:
    session_id = st.session_state.get(MEDIA_SESSION_ID_KEY)
    if not session_id:
        session_id = uuid4().hex
        st.session_state[MEDIA_SESSION_ID_KEY] = session_id
    root = Path(utils.storage_dir("six_clip_media", create=True))
    path = root / str(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _replace_segment(plan: SixClipPlan, index: int, **updates) -> SixClipPlan:
    segments = list(plan.segments)
    segments[index - 1] = segments[index - 1].model_copy(update=updates)
    return plan.model_copy(update={"segments": segments})


def render_six_clip_sections(
    target_words: int,
) -> SixClipPlan:
    plan = get_session_plan(target_words)

    st.subheader("Section 2 — Timeline Clips")
    st.caption(
        f"{len(plan.segments)} clips / {plan.timeline_duration_sec:g} seconds. "
        "Each card owns its displayed range. Add a direct media URL or upload "
        "an image/video for every clip before rendering."
    )

    _, page_count = timeline_page(plan, page=1)
    selected_page = 1
    if page_count > 1:
        selected_page = int(
            st.number_input(
                "Timeline Page",
                min_value=1,
                max_value=page_count,
                value=min(
                    int(st.session_state.get("six_clip_timeline_page", 1)),
                    page_count,
                ),
                step=1,
                key="six_clip_timeline_page",
            )
        )
        st.caption(f"Page {selected_page} of {page_count}")
    visible_segments, _ = timeline_page(plan, page=selected_page)

    updated_segments: dict[int, SixClipSegment] = {}
    session_dir = _media_session_dir()
    for segment in visible_segments:
        index = segment.index
        title_key = _widget_key(index, "title")
        narration_key = _widget_key(index, "narration")
        prompt_key = _widget_key(index, "prompt")
        st.session_state.setdefault(title_key, segment.title)
        st.session_state.setdefault(narration_key, segment.narration_context)
        st.session_state.setdefault(prompt_key, segment.video_prompt)

        with st.container(border=True):
            ready = bool(
                segment.media_path
                and Path(segment.media_path).is_file()
                and segment.media_kind in {"video", "image"}
            )
            status = "✓ Media Ready" if ready else "⚠ Media Missing"
            st.markdown(
                f"### CLIP {index} — {segment.start_sec}–{segment.end_sec} seconds  "
                f"\n{status}"
            )

            title = st.text_input(
                "Clip Title",
                key=title_key,
            ).strip()
            narration = st.text_area(
                "Narration Context",
                height=120,
                key=narration_key,
            ).strip()
            prompt = st.text_area(
                "Video Prompt (English)",
                height=260,
                key=prompt_key,
            ).strip()

            media_mode = st.radio(
                "Media Source",
                options=["URL", "Upload"],
                horizontal=True,
                key=_widget_key(index, "media_mode"),
            )
            current_kind = segment.media_kind
            current_path = segment.media_path

            if media_mode == "URL":
                media_url = st.text_input(
                    "Direct Media URL",
                    placeholder=(
                        "https://flow-content.google/video/... or a direct .mp4/.jpg/.png URL"
                    ),
                    key=_widget_key(index, "url"),
                ).strip()
                if st.button(
                    "Import Media URL",
                    key=_widget_key(index, "import_url"),
                    use_container_width=True,
                ):
                    if not media_url:
                        st.warning("Enter a direct media URL first.")
                    else:
                        try:
                            with st.spinner(f"Importing media for Clip {index}..."):
                                imported = six_clip_media.import_media_url(
                                    media_url,
                                    session_dir,
                                    clip_index=index,
                                )
                        except six_clip_media.SixClipMediaError as exc:
                            st.error(str(exc))
                        else:
                            current_kind = imported.media_kind
                            current_path = imported.local_path
                            plan = _replace_segment(
                                plan,
                                index,
                                title=title,
                                narration_context=narration,
                                video_prompt=prompt,
                                media_kind=current_kind,
                                media_path=current_path,
                            )
                            set_session_plan(plan, sync_widgets=False)
                            st.rerun()
            else:
                uploaded = st.file_uploader(
                    "Upload Image or Video",
                    type=[
                        "mp4",
                        "MP4",
                        "mov",
                        "MOV",
                        "webm",
                        "WEBM",
                        "jpg",
                        "JPG",
                        "jpeg",
                        "JPEG",
                        "png",
                        "PNG",
                        "webp",
                        "WEBP",
                    ],
                    accept_multiple_files=False,
                    key=_widget_key(index, "upload"),
                )
                if uploaded is not None:
                    try:
                        imported = six_clip_media.save_uploaded_media(
                            uploaded.name,
                            uploaded,
                            session_dir,
                            clip_index=index,
                        )
                    except six_clip_media.SixClipMediaError as exc:
                        st.error(str(exc))
                    else:
                        current_kind = imported.media_kind
                        current_path = imported.local_path

            if current_path and Path(current_path).is_file():
                if current_kind == "video":
                    st.video(current_path)
                elif current_kind == "image":
                    st.image(current_path, use_container_width=True)
                clear_col, info_col = st.columns([1, 2])
                if clear_col.button(
                    "Clear Media",
                    key=_widget_key(index, "clear_media"),
                    use_container_width=True,
                ):
                    current_kind = ""
                    current_path = ""
                    plan = _replace_segment(
                        plan,
                        index,
                        title=title,
                        narration_context=narration,
                        video_prompt=prompt,
                        media_kind="",
                        media_path="",
                    )
                    set_session_plan(plan, sync_widgets=False)
                    st.rerun()
                info_col.caption(f"Local imported copy: {Path(current_path).name}")

            updated_segments[index] = (
                SixClipSegment(
                    index=index,
                    start_sec=segment.start_sec,
                    end_sec=segment.end_sec,
                    title=title,
                    narration_context=narration,
                    video_prompt=prompt,
                    media_kind=current_kind,
                    media_path=current_path,
                )
            )

    plan = SixClipPlan(
        target_words=int(target_words),
        narration_duration_sec=plan.narration_duration_sec,
        timeline_duration_sec=plan.timeline_duration_sec,
        slot_duration_sec=plan.slot_duration_sec,
        narration_fingerprint=plan.narration_fingerprint,
        segments=[
            updated_segments.get(segment.index, segment)
            for segment in plan.segments
        ],
    )
    set_session_plan(plan, sync_widgets=False)

    missing = six_clip_media.validate_ready_media(plan)
    if missing:
        st.warning(
            "Final render is locked until media is ready for: "
            + six_clip_media.missing_media_message(plan)
        )
    else:
        st.success(
            f"All {len(plan.segments)} clips have media and are ready for final rendering."
        )

    st.subheader("Section 3 — Master Prompt")
    st.caption(
        "This is rebuilt from the current Section 2 narration and video prompts. "
        "Each copyable batch contains at most six absolute timeline clips."
    )
    for batch_index, prompt in enumerate(
        build_master_prompt_batches(plan),
        start=1,
    ):
        st.caption(f"Prompt batch {batch_index}")
        st.code(prompt, language=None, wrap_lines=True)
    return plan
