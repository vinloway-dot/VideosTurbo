from pathlib import Path

from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services.six_clip_plan import build_timeline_ranges
from webui import six_clip_timeline


MAIN_SOURCE = Path("webui/Main.py").read_text(encoding="utf-8")
TIMELINE_SOURCE = Path("webui/six_clip_timeline.py").read_text(encoding="utf-8")


def _plan(duration: float, media_paths: dict[int, str] | None = None) -> SixClipPlan:
    media_paths = media_paths or {}
    ranges = build_timeline_ranges(duration)
    return SixClipPlan(
        target_words=200,
        narration_duration_sec=duration,
        timeline_duration_sec=max(60.0, duration),
        narration_fingerprint=f"voice-{duration}",
        segments=[
            SixClipSegment(
                index=index,
                start_sec=start,
                end_sec=end,
                title=f"Clip {index}",
                media_kind="video" if index in media_paths else "",
                media_path=media_paths.get(index, ""),
            )
            for index, (start, end) in enumerate(ranges, start=1)
        ],
    )


def test_timeline_page_uses_absolute_indexes():
    visible, page_count = six_clip_timeline.timeline_page(_plan(127.0), page=2)

    assert page_count == 3
    assert [item.index for item in visible] == [7, 8, 9, 10, 11, 12]


def test_rebuild_keeps_media_only_for_identical_ranges(tmp_path):
    media_paths = {}
    for index in range(1, 8):
        media_file = tmp_path / f"clip-{index}.mp4"
        media_file.write_bytes(b"video")
        media_paths[index] = str(media_file)

    merged = six_clip_timeline.merge_media_for_unchanged_ranges(
        _plan(63.0, media_paths),
        _plan(68.0),
    )

    assert [segment.media_path for segment in merged.segments[:6]] == [
        media_paths[index] for index in range(1, 7)
    ]
    assert merged.segments[6].media_path == ""


def test_main_uses_timeline_ui_instead_of_legacy_stock_panel():
    assert "params.target_words = st.number_input(" in MAIN_SOURCE
    assert '"Target Words"' in MAIN_SOURCE
    assert "uploaded_files = _render_six_clip_video_settings(middle_panel, params)" in MAIN_SOURCE
    assert "uploaded_files = _render_video_settings(middle_panel, params)" not in MAIN_SOURCE
    assert 'params.video_source = "six_clip"' in MAIN_SOURCE
    assert "params.six_clip_mode = True" in MAIN_SOURCE


def test_generate_script_flow_is_text_only_and_confirmation_builds_plan():
    assert "six_clip_plan.build_script_generation_requirements(" in MAIN_SOURCE
    local_generation = MAIN_SOURCE.split(
        "def _render_local_script_generation", 1
    )[1].split("def _render_loomloom_candidates", 1)[0]
    assert "six_clip_plan.generate_six_clip_plan(" not in local_generation
    assert "voice.tts(" not in local_generation
    assert "def confirm_script_and_build_timeline(" in MAIN_SOURCE
    assert 'key="confirm_script_build_timeline_button"' in MAIN_SOURCE


def test_timeline_ui_has_editable_prompt_and_exactly_one_media_source_per_clip():
    assert 'st.subheader("Section 2 — Timeline Clips")' in TIMELINE_SOURCE
    assert "timeline_page(plan, page=selected_page)" in TIMELINE_SOURCE
    assert '"Narration Context"' in TIMELINE_SOURCE
    assert '"Video Prompt (English)"' in TIMELINE_SOURCE
    assert 'options=["URL", "Upload"]' in TIMELINE_SOURCE
    assert '"Import Media URL"' in TIMELINE_SOURCE
    assert '"Upload Image or Video"' in TIMELINE_SOURCE
    assert "six_clip_media.import_media_url(" in TIMELINE_SOURCE
    assert "six_clip_media.save_uploaded_media(" in TIMELINE_SOURCE


def test_url_import_does_not_mutate_instantiated_text_input_state():
    unsafe_mutation = 'st.session_state[_widget_key(index, "url")] = ""'
    assert unsafe_mutation not in TIMELINE_SOURCE


def test_timeline_ui_previews_readiness_and_builds_live_master_prompt():
    assert "st.video(current_path)" in TIMELINE_SOURCE
    assert "st.image(current_path, use_container_width=True)" in TIMELINE_SOURCE
    assert "missing = six_clip_media.validate_ready_media(plan)" in TIMELINE_SOURCE
    assert '"Final render is locked until media is ready for: "' in TIMELINE_SOURCE
    assert 'st.subheader("Section 3 — Master Prompt")' in TIMELINE_SOURCE
    assert "build_master_prompt_batches(plan)" in TIMELINE_SOURCE
    assert "for batch_index, prompt in enumerate(" in TIMELINE_SOURCE


def test_main_submits_current_six_clip_plan_to_video_params():
    assert "params.six_clip_plan = six_clip_timeline.render_six_clip_sections(" in MAIN_SOURCE
    assert "refresh_six_clip_plan" not in MAIN_SOURCE
    assert "params.six_clip_mode = True" in MAIN_SOURCE
