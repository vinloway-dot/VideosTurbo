from pathlib import Path

from webui import cloud_agent_ui
from webui.cloud_agent_ui import build_production_stages, derive_workflow_step


class ShellStreamlit:
    def __init__(self):
        self.html_bodies = []
        self.titles = []
        self.captions = []

    def html(self, body):
        self.html_bodies.append(str(body))

    def title(self, value):
        self.titles.append(value)

    def caption(self, value):
        self.captions.append(value)


def test_page_header_uses_approved_primary_copy(monkeypatch):
    fake = ShellStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", fake)

    cloud_agent_ui.render_page_header(saved=True)

    assert fake.titles == ["Create a video"]
    assert "Research, write, narrate, and produce — all in one flow." in fake.captions
    assert any("Workspace / Cloud Agent" in body for body in fake.html_bodies)
    assert any("Saved" in body for body in fake.html_bodies)


def test_cloud_agent_theme_is_a_project_local_css_asset():
    css_path = Path("webui/cloud_agent.css")
    assert css_path.is_file()
    assert "--vt-primary:" in css_path.read_text(encoding="utf-8")


def test_workflow_step_advances_only_from_accepted_local_artifacts():
    assert derive_workflow_step(False, False, None) == 1
    assert derive_workflow_step(True, False, None) == 2
    assert derive_workflow_step(True, True, None) == 3
    assert derive_workflow_step(True, False, {"id": "job-1"}) == 3


def test_production_stages_map_existing_checkpoint_without_new_backend_states():
    stages = build_production_stages(
        script_ready=True,
        prepared_voice_ready=False,
        job={
            "status": "FLOW_GENERATING",
            "checkpoint": "TTS_READY",
            "current_step": "flow_generating",
        },
    )

    assert [(stage.key, stage.state) for stage in stages] == [
        ("script", "complete"),
        ("voice", "complete"),
        ("flow", "active"),
        ("canva", "pending"),
        ("export", "pending"),
    ]


def test_failed_job_marks_only_the_current_presentation_stage_as_error():
    stages = build_production_stages(
        script_ready=True,
        prepared_voice_ready=True,
        job={
            "status": "FAILED",
            "checkpoint": "FLOW_READY",
            "current_step": "canva_assembling",
        },
    )

    assert [stage.state for stage in stages] == [
        "complete",
        "complete",
        "complete",
        "error",
        "pending",
    ]
