from pathlib import Path


UI_SOURCE = Path("webui/cloud_agent.py")
MAIN_SOURCE = Path("webui/Main.py")


def test_cloud_agent_ui_is_a_thin_fastapi_client_with_required_controls_and_status():
    source = UI_SOURCE.read_text(encoding="utf-8")

    for label in (
        "Video Subject",
        "Target Words",
        "Language",
        "Generate Script",
        "Script Editor",
        "View Master Prompt",
        "TTS Provider",
        "Voice",
        "Speed",
        "Google Flow",
        "Canva",
        "Open Browser",
        "Start",
        "Pause",
        "Resume",
        "Retry",
        "Cancel",
        "Narration Too Long",
        "shorten script",
        "reduce Target Words",
        "increase Voice Rate",
    ):
        assert label in source
    assert "/api/v1/cloud-agent/" in source
    assert "sqlite3" not in source.lower()
    assert "PersistentBrowserManager" not in source


def test_main_renders_cloud_agent_without_the_retired_local_generation_flow():
    source = MAIN_SOURCE.read_text(encoding="utf-8")
    application = source.split("def _render_application():", maxsplit=1)[1]

    assert "from webui import cloud_agent" in source
    assert "cloud_agent.render_cloud_agent_panel" in application
    assert "_render_six_clip_video_settings" not in application
    assert "_render_audio_settings" not in application
    assert "_render_subtitle_settings" not in application
    assert "_render_generation_controls" not in application


def test_main_source_has_no_retired_classic_video_generation_dependencies():
    source = MAIN_SOURCE.read_text(encoding="utf-8")

    assert "cloud_agent.render_cloud_agent_panel" in source
    for retired_symbol in (
        "_render_generation_controls",
        "_render_six_clip_video_settings",
        "local_video_materials_uploader",
        "stock_materials",
        "six_clip_plan",
        "six_clip_video_aspect_select",
    ):
        assert retired_symbol not in source
