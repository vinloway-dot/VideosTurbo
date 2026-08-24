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


def test_main_renders_the_cloud_agent_panel_without_removing_legacy_six_clip_ui():
    source = MAIN_SOURCE.read_text(encoding="utf-8")

    assert "from webui import cloud_agent" in source
    assert "cloud_agent.render_cloud_agent_panel" in source
    assert "_render_six_clip_video_settings" in source
