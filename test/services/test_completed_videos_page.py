from pathlib import Path
import runpy
from unittest.mock import Mock

import pytest

from webui import cloud_agent_ui, completed_videos


PAGE_PATH = Path("webui/pages/4_Completed_Videos.py")


def test_prompt_action_requests_one_prompt_and_keeps_video_card_unchanged(monkeypatch):
    api = Mock(return_value={"prompt": "ready image prompt"})
    monkeypatch.setattr(completed_videos, "_api", api)

    result = completed_videos.generate_thumbnail_prompt("job-1")

    assert result == "ready image prompt"
    assert api.call_args.args == (
        "POST",
        "videos/job-1/thumbnail-prompt",
    )


def test_prompt_action_error_is_scoped_to_its_job():
    state = {"thumbnail_prompt_error_by_job": {}}

    completed_videos.store_thumbnail_prompt_error(state, "job-1", "provider timeout")

    assert state["thumbnail_prompt_error_by_job"] == {"job-1": "provider timeout"}


def test_completed_videos_page_uses_shared_navigation_and_session_state(monkeypatch):
    if not PAGE_PATH.is_file():
        pytest.fail("dedicated completed-videos page is missing")

    calls = []

    class Streamlit:
        session_state = {"retained": True}

        def set_page_config(self, **kwargs):
            calls.append(("page_config", kwargs))

        def title(self, label):
            calls.append(("title", label))

        def caption(self, label):
            calls.append(("caption", label))

    fake = Streamlit()
    monkeypatch.setattr(cloud_agent_ui, "apply_cloud_agent_theme", lambda: calls.append(("theme",)))
    monkeypatch.setattr(cloud_agent_ui, "render_sidebar", lambda: calls.append(("sidebar",)))
    monkeypatch.setattr(
        completed_videos,
        "render_video_library",
        lambda *, ui_state, show_heading: calls.append(
            ("library", ui_state, show_heading)
        ),
    )
    monkeypatch.setitem(runpy.sys.modules, "streamlit", fake)

    runpy.run_path(str(PAGE_PATH), run_name="completed_videos_page_test")

    assert ("theme",) in calls
    assert ("sidebar",) in calls
    assert ("title", "วีดีโอที่สร้าง") in calls
    assert ("library", fake.session_state, False) in calls
