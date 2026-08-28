from contextlib import nullcontext
from html import unescape
from pathlib import Path

from playwright.sync_api import sync_playwright

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

    assert any(
        "Workspace / Cloud Agent" in " ".join(unescape(body).split())
        for body in fake.html_bodies
    )
    assert fake.titles == ["Create a video"]
    assert "Research, write, narrate, and produce — all in one flow." in fake.captions
    assert any("Saved" in body for body in fake.html_bodies)


def test_saved_indicator_requires_a_local_saved_draft(monkeypatch):
    fresh = ShellStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", fresh)

    assert not cloud_agent_ui.has_saved_draft({})
    cloud_agent_ui.render_page_header(saved=cloud_agent_ui.has_saved_draft({}))
    assert not any("Saved" in body for body in fresh.html_bodies)

    saved_state = {"cloud_agent_draft_script": "A saved narration draft."}
    assert cloud_agent_ui.has_saved_draft(saved_state)
    saved = ShellStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", saved)
    cloud_agent_ui.render_page_header(
        saved=cloud_agent_ui.has_saved_draft(saved_state)
    )
    assert any("Saved" in body for body in saved.html_bodies)


def test_cloud_agent_theme_is_a_project_local_css_asset():
    css_path = Path("webui/cloud_agent.css")
    assert css_path.is_file()
    assert "--vt-primary:" in css_path.read_text(encoding="utf-8")


def test_cloud_agent_text_entry_fields_use_white_surface():
    css = Path("webui/cloud_agent.css").read_text(encoding="utf-8")

    assert (
        'div[class*="st-key-cloud_agent_"] [data-testid="stTextInput"] input,'
        in css
    )
    assert 'background: var(--vt-surface) !important;' in css


def test_cloud_agent_theme_includes_responsive_and_reduced_motion_rules():
    css = Path("webui/cloud_agent.css").read_text(encoding="utf-8")
    cloud_agent_source = Path("webui/cloud_agent.py").read_text(encoding="utf-8")

    assert 'with st.container(key="cloud_agent_workspace"):' in cloud_agent_source
    assert "@media (max-width: 1100px)" in css
    assert "flex-wrap: wrap;" in css
    assert "flex: 1 1 100% !important;" in css
    assert "@media (max-width: 760px)" in css
    assert ".vt-workflow { grid-template-columns: 1fr; }" in css
    assert "div[class*=\"st-key-cloud_agent_\"] button { width: 100%; }" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition-duration: 0.01ms !important;" in css
    assert "animation-iteration-count: 1 !important;" in css


def test_cloud_agent_workspace_columns_stack_at_tablet_widths():
    css = Path("webui/cloud_agent.css").read_text(encoding="utf-8")
    markup = f"""
        <style>{css}</style>
        <div class="st-key-cloud_agent_workspace">
          <div data-testid="stHorizontalBlock" style="display: flex">
            <div data-testid="stColumn">Brief</div>
            <div data-testid="stColumn">Generation</div>
          </div>
        </div>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/usr/bin/google-chrome", headless=True
        )
        page = browser.new_page(viewport={"width": 1099, "height": 600})
        page.set_content(markup)
        tablet_boxes = page.locator('[data-testid="stColumn"]').evaluate_all(
            "elements => elements.map(element => element.getBoundingClientRect().toJSON())"
        )
        page.set_viewport_size({"width": 759, "height": 600})
        mobile_boxes = page.locator('[data-testid="stColumn"]').evaluate_all(
            "elements => elements.map(element => element.getBoundingClientRect().toJSON())"
        )
        browser.close()

    assert tablet_boxes[1]["y"] > tablet_boxes[0]["y"]
    assert mobile_boxes[1]["y"] > mobile_boxes[0]["y"]


def test_mobile_sidebar_expand_control_remains_visible_and_reachable():
    css = Path("webui/cloud_agent.css").read_text(encoding="utf-8")
    markup = f"""
        <style>{css}</style>
        <header data-testid="stHeader">
          <div data-testid="stToolbar">
            <button data-testid="stExpandSidebarButton" type="button">☰</button>
          </div>
          <div data-testid="stDecoration">Decoration</div>
        </header>
    """

    sizes = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/usr/bin/google-chrome", headless=True
        )
        page = browser.new_page(viewport={"width": 759, "height": 600})
        page.set_content(markup)
        control = page.locator('[data-testid="stExpandSidebarButton"]')
        for width in (759, 360):
            page.set_viewport_size({"width": width, "height": 600})
            sizes.append(
                control.evaluate(
                    """element => {
                        const rect = element.getBoundingClientRect();
                        return {width: rect.width, height: rect.height};
                    }"""
                )
            )
        browser.close()

    assert all(
        box["width"] > 0 and box["height"] > 0 for box in sizes
    )


def test_workflow_step_advances_only_from_accepted_local_artifacts():
    assert derive_workflow_step(False, False, None) == 1
    assert derive_workflow_step(True, False, None) == 2
    assert derive_workflow_step(True, True, None) == 3
    assert derive_workflow_step(True, False, {"id": "job-1"}) == 3


def test_fresh_completed_job_completes_workflow_and_all_production_stages():
    job = {
        "id": "job-complete",
        "status": "COMPLETED",
        "checkpoint": "COMPLETED",
        "current_step": "completed",
    }

    assert derive_workflow_step(False, False, job) == 4
    assert [
        stage.state
        for stage in build_production_stages(
            script_ready=False,
            prepared_voice_ready=False,
            job=job,
        )
    ] == ["complete"] * 5


def test_completed_job_remains_complete_with_a_local_script():
    job = {
        "id": "job-complete",
        "status": "COMPLETED",
        "checkpoint": "FINAL_VALIDATED",
        "current_step": "completed",
    }

    assert derive_workflow_step(True, False, job) == 4
    assert [
        stage.state
        for stage in build_production_stages(
            script_ready=True,
            prepared_voice_ready=False,
            job=job,
        )
    ] == ["complete"] * 5


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


def test_production_status_renders_five_fixed_safe_stages(monkeypatch):
    class StatusStreamlit:
        def __init__(self):
            self.html_bodies = []

        def html(self, body):
            self.html_bodies.append(str(body))

    fake = StatusStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", fake)
    stages = cloud_agent_ui.build_production_stages(
        script_ready=True,
        prepared_voice_ready=True,
        job={"status": "QUEUED", "checkpoint": "NONE", "current_step": "queued"},
    )

    cloud_agent_ui.render_production_status(
        stages, {"id": "job-123", "status": "QUEUED"}
    )

    body = " ".join(fake.html_bodies)
    assert all(label in body for label in ("Script", "Voice", "Flow", "Canva", "Export"))
    assert "job-123" in body


def test_workflow_and_production_states_have_non_color_semantics(monkeypatch):
    fake = ShellStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", fake)

    cloud_agent_ui.render_workflow_rail(2)
    cloud_agent_ui.render_production_status(
        (
            cloud_agent_ui.StageView("script", "Script", "complete"),
            cloud_agent_ui.StageView("voice", "Voice", "active"),
            cloud_agent_ui.StageView("flow", "Flow", "pending"),
            cloud_agent_ui.StageView("canva", "Canva", "pending"),
            cloud_agent_ui.StageView("export", "Export", "pending"),
        ),
        {"id": "job-123", "status": "TTS_GENERATING"},
    )
    cloud_agent_ui.render_production_status(
        (
            cloud_agent_ui.StageView("script", "Script", "complete"),
            cloud_agent_ui.StageView("voice", "Voice", "complete"),
            cloud_agent_ui.StageView("flow", "Flow", "error"),
            cloud_agent_ui.StageView("canva", "Canva", "pending"),
            cloud_agent_ui.StageView("export", "Export", "pending"),
        ),
        {"id": "job-123", "status": "FAILED"},
    )

    workflow, production, failed_production = fake.html_bodies
    assert 'role="list"' in workflow
    assert workflow.count('aria-current="step"') == 1
    assert all(state in workflow for state in ("Complete", "Current", "Upcoming"))
    assert all(
        label in production
        for label in (
            'aria-label="Script: Complete"',
            'aria-label="Voice: In progress"',
            'aria-label="Flow: Pending"',
            'aria-label="Canva: Pending"',
        )
    )
    assert production.count('aria-current="step"') == 1
    assert all(indicator in production for indicator in (">✓<", ">▶<", ">○<"))
    assert 'aria-label="Flow: Error"' in failed_production
    assert failed_production.count('aria-current="step"') == 1
    assert ">!<" in failed_production


def test_sidebar_disabled_items_use_local_symbols_instead_of_font_ligatures(
    monkeypatch,
):
    class SidebarStreamlit:
        def __init__(self):
            self.sidebar = nullcontext()
            self.html_bodies = []

        def html(self, body):
            self.html_bodies.append(str(body))

        def page_link(self, *_args, **_kwargs):
            return None

    fake = SidebarStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", fake)

    cloud_agent_ui.render_sidebar()

    body = " ".join(fake.html_bodies)
    assert "Projects" in body
    assert "material-symbols" not in body
    assert ">folder<" not in body and ">settings<" not in body


def test_sidebar_settings_is_a_real_page_link(monkeypatch):
    class SidebarStreamlit:
        def __init__(self):
            self.sidebar = nullcontext()
            self.links = []

        def page_link(self, page, **kwargs):
            self.links.append((page, kwargs))

        def html(self, *_args, **_kwargs):
            return None

    fake = SidebarStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", fake)

    cloud_agent_ui.render_sidebar()

    assert any(
        page == "pages/3_Settings.py" and kwargs.get("label") == "Settings"
        for page, kwargs in fake.links
    )


def test_settings_page_is_present_and_reuses_cloud_agent_settings_renderer():
    settings_page = Path("webui/pages/3_Settings.py")
    assert settings_page.is_file()
    source = settings_page.read_text(encoding="utf-8")
    renderer = Path("webui/cloud_agent.py").read_text(encoding="utf-8")
    assert "_render_advanced_settings" in source
    assert "Research Settings" in renderer
    assert "TTS Provider Settings" in renderer
    assert "Research API Key" in renderer
    assert "include_research=True" in source


def test_research_summary_never_contains_raw_source_body_or_secret_fields():
    summary = cloud_agent_ui.research_summary(
        research_draft_id="draft-1",
        sources=[
            {
                "title": "Rice guide",
                "url": "https://example.com/rice",
                "content_hash": "a" * 64,
                "body": "must not render",
                "api_key": "must not render",
            }
        ],
        accounting={"provider_rounds": 2, "tool_calls": 1},
    )

    assert summary.status == "Research complete"
    assert summary.source_count == 1
    assert summary.source_links == (("Rice guide", "https://example.com/rice"),)
    assert "must not render" not in repr(summary)
