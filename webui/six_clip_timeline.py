from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import streamlit as st

from app.models.six_clip import SixClipPlan, SixClipSegment, empty_six_clip_plan
from app.services import six_clip_media
from app.services.six_clip_plan import build_master_prompt
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
    *,
    refresh_plan=None,
) -> SixClipPlan:
    plan = get_session_plan(target_words)

    st.subheader("Section 2 — Six Video Clips")
    st.caption(
        "Each clip owns exactly one 10-second visual slot. Add a direct media URL "
        "or upload an image/video for every clip before rendering."
    )

    if refresh_plan is not None:
        if st.button(
            "Generate / Refresh 6 Clip Prompts with AI",
            key="six_clip_refresh_prompts",
            use_container_width=True,
            icon=":material/auto_awesome:",
        ):
            try:
                with st.spinner("Analyzing the script into six visual clips..."):
                    refreshed = refresh_plan()
            except Exception as exc:
                st.error(f"Failed to generate six clip prompts: {exc}")
            else:
                set_session_plan(refreshed, sync_widgets=True)
                st.rerun()

    updated_segments: list[SixClipSegment] = []
    session_dir = _media_session_dir()
    for segment in plan.segments:
        index = segment.index
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
                value=segment.title,
                key=_widget_key(index, "title"),
            ).strip()
            narration = st.text_area(
                "Narration Context",
                value=segment.narration_context,
                height=120,
                key=_widget_key(index, "narration"),
            ).strip()
            prompt = st.text_area(
                "Video Prompt (English)",
                value=segment.video_prompt,
                height=260,
                key=_widget_key(index, "prompt"),
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
                            st.session_state[_widget_key(index, "url")] = ""
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

            updated_segments.append(
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

    plan = SixClipPlan(target_words=int(target_words), segments=updated_segments)
    set_session_plan(plan, sync_widgets=False)

    missing = six_clip_media.validate_ready_media(plan)
    if missing:
        st.warning(
            "Final render is locked until media is ready for: "
            + six_clip_media.missing_media_message(plan)
        )
    else:
        st.success("All six clips have media and are ready for final rendering.")

    st.subheader("Section 3 — Master Prompt")
    st.caption(
        "This is rebuilt from the current Section 2 narration and video prompts. "
        "Use the copy button in the code block to send all six prompts to an AI video tool."
    )
    st.code(build_master_prompt(plan), language=None, wrap_lines=True)
    return plan
