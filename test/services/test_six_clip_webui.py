from pathlib import Path


MAIN_SOURCE = Path("webui/Main.py").read_text(encoding="utf-8")
TIMELINE_SOURCE = Path("webui/six_clip_timeline.py").read_text(encoding="utf-8")


def test_main_uses_fixed_six_clip_ui_instead_of_legacy_stock_panel():
    assert "params.target_words = st.number_input(" in MAIN_SOURCE
    assert '"Target Words"' in MAIN_SOURCE
    assert "uploaded_files = _render_six_clip_video_settings(middle_panel, params)" in MAIN_SOURCE
    assert "uploaded_files = _render_video_settings(middle_panel, params)" not in MAIN_SOURCE
    assert 'params.video_source = "six_clip"' in MAIN_SOURCE
    assert "params.six_clip_mode = True" in MAIN_SOURCE


def test_generate_script_flow_populates_six_clip_plan():
    assert "six_clip_plan.build_script_generation_requirements(" in MAIN_SOURCE
    assert "clip_plan = six_clip_plan.generate_six_clip_plan(" in MAIN_SOURCE
    assert "six_clip_timeline.set_session_plan(clip_plan, sync_widgets=True)" in MAIN_SOURCE
    assert "generate_script_terms_and_six_clip_plan" in MAIN_SOURCE


def test_timeline_ui_has_editable_prompt_and_exactly_one_media_source_per_clip():
    assert 'st.subheader("Section 2 — Six Video Clips")' in TIMELINE_SOURCE
    assert "for segment in plan.segments:" in TIMELINE_SOURCE
    assert '"Narration Context"' in TIMELINE_SOURCE
    assert '"Video Prompt (English)"' in TIMELINE_SOURCE
    assert 'options=["URL", "Upload"]' in TIMELINE_SOURCE
    assert '"Import Media URL"' in TIMELINE_SOURCE
    assert '"Upload Image or Video"' in TIMELINE_SOURCE
    assert "six_clip_media.import_media_url(" in TIMELINE_SOURCE
    assert "six_clip_media.save_uploaded_media(" in TIMELINE_SOURCE


def test_timeline_ui_previews_readiness_and_builds_live_master_prompt():
    assert "st.video(current_path)" in TIMELINE_SOURCE
    assert "st.image(current_path, use_container_width=True)" in TIMELINE_SOURCE
    assert "missing = six_clip_media.validate_ready_media(plan)" in TIMELINE_SOURCE
    assert '"Final render is locked until media is ready for: "' in TIMELINE_SOURCE
    assert 'st.subheader("Section 3 — Master Prompt")' in TIMELINE_SOURCE
    assert "st.code(build_master_prompt(plan), language=None, wrap_lines=True)" in TIMELINE_SOURCE


def test_main_submits_current_six_clip_plan_to_video_params():
    assert "params.six_clip_plan = six_clip_timeline.render_six_clip_sections(" in MAIN_SOURCE
    assert "params.six_clip_mode = True" in MAIN_SOURCE
