# Cloud Agent Modern UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the existing Cloud Agent Streamlit page to closely match the approved modern SaaS mockup while preserving every current API, Research, TTS, and worker behavior.

**Architecture:** Keep `webui/cloud_agent.py` as the thin FastAPI client and interaction controller, add one presentation module for deterministic stage/view data and shell rendering, and add one Cloud-Agent-scoped stylesheet. Recompose the existing controls into keyed Streamlit containers and responsive columns; do not change backend contracts or add a frontend framework.

**Tech Stack:** Python 3.11+, Streamlit 1.59.1, existing FastAPI client helpers, HTML/CSS through `st.html`, Material Symbols supported by Streamlit, pytest, Streamlit AppTest, Ruff, headless Google Chrome for local visual verification.

**Spec:** `docs/superpowers/specs/2026-08-27-cloud-agent-modern-ui-design.md`

**Approved visual reference:** `docs/ui-reference/cloud-agent-modern-ui-approved.png`

## Global Constraints

- Keep `webui/cloud_agent.py` as a FastAPI-only client; do not import backend stores, factories, browser managers, or SQLite.
- Do not modify `app/controllers`, `app/services`, `app/models`, worker code, database schemas, provider adapters, or deployment units.
- Preserve every existing API path, JSON field, timeout, typed error message, provider limit, no-fallback rule, and Research-to-Script-Editor handoff.
- Preserve existing widget keys, especially `cloud_agent_script_mode`, `cloud_agent_script`, `cloud_agent_generate_research_script`, `cloud_agent_create_voice`, and `cloud_agent_start`.
- Keep provider keys and TTS secrets write-only; never put a secret into HTML, CSS, logs, screenshots, status models, or test fixtures.
- Use Streamlit native widgets for all interactive controls. Custom HTML is presentation-only and must contain only escaped fixed labels or safe derived status text.
- Add no JavaScript, remote font, CSS framework, analytics, frontend framework, or runtime dependency.
- Automated tests and visual smoke checks must not call OpenRouter, AIHubMix generation, TTS synthesis, Google Flow generation, Canva mutation, or browser-session endpoints.
- Scope CSS under Cloud Agent keys/test IDs so Music Batch retains its current styling and behavior.
- Implement every production change with RED → observed failure → smallest GREEN → focused regression → reviewer gate → commit.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `webui/Main.py` | Page config plus Cloud Agent application shell entry point. |
| `webui/cloud_agent.py` | Existing API calls, widget state, actions, and recomposed section renderers. |
| `webui/cloud_agent_ui.py` | New presentation-only stage models, safe shell/status renderers, and CSS loader. |
| `webui/cloud_agent.css` | New Cloud-Agent-scoped design tokens, layout styling, responsive rules, and accessibility states. |
| `test/services/test_cloud_agent_ui.py` | New pure presentation/state mapping and shell tests. |
| `test/services/test_cloud_agent_webui.py` | Existing API/state tests plus section-layout interaction regression tests. |
| `test/services/test_webui_startup.py` | Existing external-directory AppTest plus modern shell startup assertion. |
| `docs/ui-reference/cloud-agent-modern-ui-approved.png` | Immutable approved visual target. |
| `docs/ui-reference/cloud-agent-modern-ui-implemented.png` | Final local screenshot captured after implementation. |

---

### Task 1: Add Deterministic UI Stage Contracts

**Files:**

- Create: `webui/cloud_agent_ui.py`
- Create: `test/services/test_cloud_agent_ui.py`

**Interfaces:**

- Consumes: safe UI values only: `script_ready: bool`, `prepared_voice_ready: bool`, and an optional job dictionary returned by the existing API.
- Produces: `StageView`, `derive_workflow_step(...) -> int`, and `build_production_stages(...) -> tuple[StageView, ...]` for later render tasks.

- [ ] **Step 1: Write failing workflow and production-stage tests**

```python
from webui.cloud_agent_ui import build_production_stages, derive_workflow_step


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
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/pytest -q test/services/test_cloud_agent_ui.py
```

Expected: collection fails because `webui.cloud_agent_ui` does not exist.

- [ ] **Step 3: Implement the stage contracts without Streamlit side effects**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


StageState = Literal["complete", "active", "pending", "error"]


@dataclass(frozen=True)
class StageView:
    key: str
    label: str
    state: StageState


_CHECKPOINT_RANK = {
    "NONE": 0,
    "PREFLIGHT_PASSED": 1,
    "TTS_READY": 2,
    "FLOW_READY": 3,
    "FINAL_VALIDATED": 4,
    "COMPLETED": 5,
}


def derive_workflow_step(
    script_ready: bool,
    prepared_voice_ready: bool,
    job: dict | None,
) -> int:
    if not script_ready:
        return 1
    if not prepared_voice_ready and not (job or {}).get("id"):
        return 2
    return 3


def build_production_stages(
    *,
    script_ready: bool,
    prepared_voice_ready: bool,
    job: dict | None,
) -> tuple[StageView, ...]:
    snapshot = dict(job or {})
    checkpoint_rank = _CHECKPOINT_RANK.get(str(snapshot.get("checkpoint") or "NONE"), 0)
    complete = {
        "script": bool(script_ready),
        "voice": bool(prepared_voice_ready or checkpoint_rank >= 2),
        "flow": checkpoint_rank >= 3,
        "canva": checkpoint_rank >= 4,
        "export": checkpoint_rank >= 5,
    }
    order = ("script", "voice", "flow", "canva", "export")
    labels = {
        "script": "Script",
        "voice": "Voice",
        "flow": "Flow",
        "canva": "Canva",
        "export": "Export",
    }
    active = next((key for key in order if not complete[key]), "export")
    if snapshot.get("status") == "FAILED":
        current = str(snapshot.get("current_step") or "")
        active = (
            "canva" if "canva" in current
            else "flow" if "flow" in current
            else "voice" if "tts" in current or "voice" in current
            else "export" if "export" in current or "validat" in current
            else active
        )
    return tuple(
        StageView(
            key=key,
            label=labels[key],
            state=(
                "complete" if complete[key]
                else "error" if key == active and snapshot.get("status") == "FAILED"
                else "active" if key == active
                else "pending"
            ),
        )
        for key in order
    )
```

- [ ] **Step 4: Run focused tests and Ruff**

Run:

```bash
.venv/bin/pytest -q test/services/test_cloud_agent_ui.py
.venv/bin/ruff check webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py
```

Expected: all tests pass and Ruff prints `All checks passed!`.

- [ ] **Step 5: Commit Task 1**

```bash
git add webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py
git commit -m "feat: add cloud agent ui stage models"
```

---

### Task 2: Build the Application Shell and Scoped Theme

**Files:**

- Create: `webui/cloud_agent.css`
- Modify: `webui/cloud_agent_ui.py`
- Modify: `webui/Main.py:15-32`
- Modify: `test/services/test_cloud_agent_ui.py`
- Modify: `test/services/test_webui_startup.py`

**Interfaces:**

- Consumes: Streamlit 1.59.1 `st.html`, `st.sidebar`, `st.page_link`, `st.title`, and `st.caption`.
- Produces: `apply_cloud_agent_theme()`, `render_sidebar()`, `render_page_header(saved: bool)`, and `render_workflow_rail(active_step: int)`.

- [ ] **Step 1: Write failing shell tests**

```python
from pathlib import Path

from webui import cloud_agent_ui


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
```

Extend the subprocess AppTest in `test_webui_startup.py` with:

```python
assert app.title[0].value == "Create a video"
assert any(
    "Research, write, narrate, and produce" in item.value
    for item in app.caption
)
```

- [ ] **Step 2: Run shell tests and verify RED**

Run:

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_ui.py \
  test/services/test_webui_startup.py
```

Expected: failures report missing render helpers, missing CSS, and the old `VideosTurbo` title.

- [ ] **Step 3: Add the theme entry points and shell renderers**

Add to `webui/cloud_agent_ui.py`:

```python
from html import escape
from pathlib import Path

import streamlit as st


_CSS_PATH = Path(__file__).with_name("cloud_agent.css")


def apply_cloud_agent_theme() -> None:
    st.html(_CSS_PATH)


def render_sidebar() -> None:
    with st.sidebar:
        st.html('<div class="vt-wordmark"><span>◆</span> VideosTurbo</div>')
        st.page_link("Main.py", label="Cloud Agent", icon=":material/auto_awesome:")
        st.page_link(
            "pages/2_Music_Batch.py",
            label="Music Batch",
            icon=":material/music_note:",
        )
        st.html(
            '<div class="vt-nav-disabled" aria-disabled="true">'
            '<span class="material-symbols-rounded">folder</span> Projects'
            '</div>'
            '<div class="vt-nav-disabled" aria-disabled="true">'
            '<span class="material-symbols-rounded">settings</span> Settings'
            '</div>'
            '<div class="vt-system-status">'
            '<span aria-hidden="true"></span> All systems operational'
            '</div>'
        )


def render_page_header(*, saved: bool) -> None:
    st.html('<div class="vt-breadcrumb">Workspace&nbsp;&nbsp;/&nbsp;&nbsp;Cloud Agent</div>')
    st.title("Create a video")
    st.caption("Research, write, narrate, and produce — all in one flow.")
    if saved:
        st.html('<div class="vt-saved"><span>✓</span> Saved</div>')


def render_workflow_rail(active_step: int) -> None:
    labels = ("Script & Research", "Voice", "Produce")
    items = []
    for index, label in enumerate(labels, start=1):
        state = "active" if index == active_step else "complete" if index < active_step else "pending"
        items.append(
            f'<div class="vt-workflow__item vt-workflow__item--{state}">'
            f'<span>{index}</span><strong>{escape(label)}</strong></div>'
        )
    st.html(f'<div class="vt-workflow">{"".join(items)}</div>')
```

- [ ] **Step 4: Create the initial scoped stylesheet**

Create `webui/cloud_agent.css` with these concrete tokens and baseline rules:

```css
:root {
    --vt-bg: #f5f7fb;
    --vt-surface: #ffffff;
    --vt-surface-muted: #f8faff;
    --vt-text: #101b35;
    --vt-text-muted: #667085;
    --vt-border: #e3e8f2;
    --vt-primary: #155eef;
    --vt-primary-hover: #004eea;
    --vt-success: #12a150;
    --vt-danger: #d92d20;
    --vt-radius: 14px;
    --vt-shadow: 0 10px 30px rgba(16, 24, 40, 0.055);
}

header[data-testid="stHeader"],
div[data-testid="stToolbar"],
div[data-testid="stDecoration"],
section[data-testid="stSidebar"] nav[data-testid="stSidebarNav"] {
    display: none !important;
}

.stApp { background: var(--vt-bg); color: var(--vt-text); }

div[data-testid="stMainBlockContainer"] {
    max-width: 1440px;
    padding: 1.5rem 2rem 2rem;
}

section[data-testid="stSidebar"] {
    border-right: 1px solid var(--vt-border);
    background: var(--vt-surface);
}

.vt-wordmark { font-size: 1.35rem; font-weight: 800; padding: 1rem 0.25rem 1.5rem; }
.vt-wordmark span { color: var(--vt-primary); }
.vt-breadcrumb, .vt-saved { color: var(--vt-text-muted); font-size: 0.82rem; font-weight: 600; }
.vt-saved span { color: var(--vt-success); }
.vt-nav-disabled { color: #98a2b3; padding: 0.7rem 0.75rem; cursor: not-allowed; }
.vt-system-status { margin-top: 40vh; border: 1px solid var(--vt-border); border-radius: 12px; padding: 0.8rem; color: var(--vt-text-muted); }
.vt-system-status > span { display: inline-block; width: 0.55rem; height: 0.55rem; border-radius: 50%; margin-right: 0.5rem; background: #2bc46d; }

.vt-workflow {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
    margin: 1rem 0;
    padding: 0.85rem 1rem;
    border: 1px solid var(--vt-border);
    border-radius: var(--vt-radius);
    background: var(--vt-surface);
}

.vt-workflow__item { display: flex; align-items: center; gap: 0.7rem; color: #667085; }
.vt-workflow__item > span { display: grid; width: 2rem; height: 2rem; place-items: center; border: 1px solid #d0d5dd; border-radius: 50%; }
.vt-workflow__item--active { color: var(--vt-primary); }
.vt-workflow__item--active > span { color: white; border-color: var(--vt-primary); background: var(--vt-primary); }
.vt-workflow__item--complete > span { color: var(--vt-primary); border-color: #b2ccff; background: #eff4ff; }

div[class*="st-key-cloud_agent_"] button[kind="primary"] {
    min-height: 2.65rem;
    border-color: var(--vt-primary);
    border-radius: 9px;
    background: var(--vt-primary);
}

div[class*="st-key-cloud_agent_"] button:focus-visible,
div[class*="st-key-cloud_agent_"] input:focus-visible,
div[class*="st-key-cloud_agent_"] textarea:focus-visible {
    outline: 3px solid rgba(21, 94, 239, 0.24) !important;
    outline-offset: 2px;
}
```

- [ ] **Step 5: Replace the old title-only entry point**

Change `webui/Main.py` so `_render_application()` is:

```python
def _render_application():
    """Render the retained Cloud Agent entry point."""
    cloud_agent_ui.apply_cloud_agent_theme()
    cloud_agent_ui.render_sidebar()
    cloud_agent_ui.render_page_header(
        saved=bool(st.session_state.get("cloud_agent_draft_script"))
    )
    cloud_agent.render_cloud_agent_panel()
```

Import `cloud_agent_ui` beside `cloud_agent`; do not change `root_dir` handling or `st.set_page_config`.

- [ ] **Step 6: Run shell regressions and commit Task 2**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_ui.py \
  test/services/test_webui_startup.py \
  test/services/test_cloud_agent_webui.py::test_main_renders_cloud_agent_without_the_retired_local_generation_flow
.venv/bin/ruff check webui/Main.py webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py test/services/test_webui_startup.py
git diff --check
git add webui/Main.py webui/cloud_agent_ui.py webui/cloud_agent.css test/services/test_cloud_agent_ui.py test/services/test_webui_startup.py
git commit -m "feat: add modern cloud agent shell"
```

Expected: all selected tests and checks pass.

---

### Task 3: Recompose Video Brief and Script Creation

**Files:**

- Modify: `webui/cloud_agent.py:503-824`
- Modify: `webui/cloud_agent.css`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**

- Consumes: existing defaults, Research settings/catalog, session-state keys, `_prepare_draft`, `_prepare_research_draft`, and safe error helpers.
- Produces: `_BriefSelection` and `_render_video_brief(ui_state, defaults, research_settings, research_provider_catalog) -> _BriefSelection` for the editor and generation setup tasks.

- [ ] **Step 1: Write failing layout and mode tests**

Add a focused Streamlit fake for the new mode-control boundary, then assert the retained key and selectable options:

```python
class ModeStreamlit:
    def __init__(self):
        self.calls = []

    def segmented_control(self, label, options, **kwargs):
        self.calls.append((label, list(options), kwargs))
        return "Research Script"


def test_script_mode_uses_approved_segmented_control_and_retained_key(monkeypatch):
    fake = ModeStreamlit()
    monkeypatch.setattr(cloud_agent, "st", fake)

    selected = cloud_agent._render_script_mode_control(
        ["Standard Script", "Research Script"],
        "Standard Script",
    )

    assert selected == "Research Script"
    assert fake.calls == [
        (
            "Script creation mode",
            ["Standard Script", "Research Script"],
            {
                "default": "Standard Script",
                "key": "cloud_agent_script_mode",
                "width": "stretch",
                "label_visibility": "collapsed",
            },
        )
    ]
```

Retain the existing assertions that Standard mode hides Research provider, API key, source, settings, and generation controls.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_webui.py::test_script_mode_uses_approved_segmented_control_and_retained_key \
  test/services/test_cloud_agent_webui.py::test_standard_mode_hides_research_only_controls \
  test/services/test_cloud_agent_webui.py::test_research_failure_never_stores_draft
```

Expected: the new test fails because the page still uses an unkeyed linear layout and `st.radio`.

- [ ] **Step 3: Extract and render the brief card while preserving handlers**

Introduce these boundaries in `webui/cloud_agent.py`. Move the current subject, word, language, custom prompt, Standard generation, and Research generation blocks into `_render_video_brief` without changing request construction:

```python
@dataclass(frozen=True)
class _BriefSelection:
    subject: str
    words: int
    language: str
    script_mode: str
    custom_system_prompt: str
    research_provider: str = ""
    research_model: str = ""


def _render_script_mode_control(options, default):
    return st.segmented_control(
        "Script creation mode",
        options,
        default=default,
        key="cloud_agent_script_mode",
        width="stretch",
        label_visibility="collapsed",
    )


def _render_video_brief(
    *,
    ui_state,
    defaults,
    research_settings,
    research_provider_catalog,
):
    with st.container(key="cloud_agent_brief_card", border=True):
        st.subheader("Video brief")
        subject = st.text_area(
            "Video subject",
            key="cloud_agent_subject",
            height=82,
            placeholder="e.g., How to cook perfect rice every time",
        )
        brief_columns = st.columns([1.8, 0.55, 0.75], gap="medium")
        words = brief_columns[1].number_input(
            "Target words",
            min_value=1,
            value=130,
            key="cloud_agent_words",
        )
        language = brief_columns[2].selectbox(
            "Language",
            options=list(language_labels),
            format_func=lambda value: language_labels[value],
            key="cloud_agent_language",
        )
        script_mode = _render_script_mode_control(
            _research_mode_options(research_settings["enabled"]),
            ui_state.get("cloud_agent_script_mode", "Standard Script"),
        )
        research_provider = ""
        research_model = ""
        # Move lines 593–824 of the pre-task file into the corresponding
        # Standard/Research branch here. Keep every existing widget key and
        # API-helper call; set research_provider/research_model from the selected
        # Research controls before returning.
        return _BriefSelection(
            subject=subject,
            words=words,
            language=language,
            script_mode=str(script_mode or "Standard Script"),
            custom_system_prompt=custom_system_prompt,
            research_provider=research_provider,
            research_model=research_model,
        )
```

Put the Video subject widget in `brief_columns[0]` if the three-field row is visually balanced at 1536 px; otherwise keep it full width above the Target words/Language row. Do not alter the returned values or API payloads.

Use exact primary actions:

```python
st.button(
    "Generate script",
    key="cloud_agent_generate_script",
    type="primary",
    icon=":material/edit_note:",
    width="stretch",
)

st.button(
    "Generate research script",
    key="cloud_agent_generate_research_script",
    type="primary",
    icon=":material/auto_awesome:",
    width="stretch",
)
```

- [ ] **Step 4: Style the brief and Research source area**

Add scoped rules for `cloud_agent_brief_card`, `cloud_agent_research_source_*`, and the segmented control. Use the existing URL row count of 1–3, label each row `Source URL N`, place the remove/count action beside the rows, and keep `Up to 3 sources · Direct webpages and PDFs` visible. Keep the citation checkbox in Research mode.

- [ ] **Step 5: Run the complete WebUI test file and commit Task 3**

```bash
.venv/bin/pytest -q test/services/test_cloud_agent_webui.py
.venv/bin/ruff check webui/cloud_agent.py test/services/test_cloud_agent_webui.py
git diff --check
git add webui/cloud_agent.py webui/cloud_agent.css test/services/test_cloud_agent_webui.py
git commit -m "feat: redesign cloud agent video brief"
```

Expected: all WebUI tests pass; request payload assertions remain unchanged.

---

### Task 4: Redesign Script Editor and Research Provenance

**Files:**

- Modify: `webui/cloud_agent.py:341-377, 824-852`
- Modify: `webui/cloud_agent_ui.py`
- Modify: `webui/cloud_agent.css`
- Modify: `test/services/test_cloud_agent_ui.py`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**

- Consumes: `cloud_agent_script`, `cloud_agent_draft_script`, Research draft ID, safe sources, and safe accounting already stored in session state.
- Produces: a keyed `Script editor` card plus compact safe Research metadata; no mutation of Research provenance rules.

- [ ] **Step 1: Write failing Research-status tests**

```python
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
```

Add a render regression that verifies `cloud_agent_script` remains the editor key and a Research failure does not call `_store_draft`.

- [ ] **Step 2: Run the tests and verify RED**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_ui.py::test_research_summary_never_contains_raw_source_body_or_secret_fields \
  test/services/test_cloud_agent_webui.py::test_research_failure_never_stores_draft
```

Expected: the presentation summary type/function is missing.

- [ ] **Step 3: Add a sanitized Research summary view model**

Implement `ResearchSummary` and `research_summary` in `webui/cloud_agent_ui.py`:

```python
@dataclass(frozen=True)
class ResearchSummary:
    status: str
    source_count: int
    source_links: tuple[tuple[str, str], ...]
    provider_rounds: int | None
    tool_calls: int | None


def research_summary(*, research_draft_id, sources, accounting) -> ResearchSummary:
    safe_links = tuple(
        (
            str(source.get("title") or source.get("url") or "Untitled source"),
            str(source.get("url") or ""),
        )
        for source in list(sources or [])
        if str(source.get("url") or "").startswith(("http://", "https://"))
    )
    safe_accounting = dict(accounting or {})
    return ResearchSummary(
        status="Research complete" if str(research_draft_id or "").strip() else "",
        source_count=len(safe_links),
        source_links=safe_links,
        provider_rounds=safe_accounting.get("provider_rounds"),
        tool_calls=safe_accounting.get("tool_calls"),
    )
```

- [ ] **Step 4: Extract the existing refresh action and render the approved Script editor card**

Move the current Refresh Draft branch into this exact helper:

```python
def _refresh_script_editor(*, subject, language, words, custom_system_prompt):
    script_for_refresh = str(st.session_state.get("cloud_agent_script", ""))
    if not script_for_refresh.strip():
        st.error("Script Editor is required before refreshing the draft.")
        return
    try:
        _store_refreshed_draft(
            _prepare_draft(
                subject=subject,
                language=language,
                target_words=words,
                script=script_for_refresh,
                custom_system_prompt=custom_system_prompt,
            )
        )
        st.rerun()
    except requests.RequestException as exc:
        st.error(_api_error_message(exc))


def _render_script_editor(*, brief, ui_state):
    with st.container(key="cloud_agent_script_card", border=True):
        title_row = st.columns([1, 0.24], vertical_alignment="center")
        title_row[0].subheader("Script editor")
        if title_row[1].button(
            "Regenerate",
            key="cloud_agent_refresh_draft",
            icon=":material/refresh:",
            width="stretch",
        ):
            _refresh_script_editor(
                subject=brief.subject,
                language=brief.language,
                words=brief.words,
                custom_system_prompt=brief.custom_system_prompt,
            )
        if brief.script_mode == "Research Script":
            summary = cloud_agent_ui.research_summary(
                research_draft_id=ui_state.get("cloud_agent_research_draft_id"),
                sources=ui_state.get("cloud_agent_research_sources", []),
                accounting=ui_state.get("cloud_agent_research_accounting", {}),
            )
            cloud_agent_ui.render_research_summary(summary)
        script = st.text_area(
            "Script",
            key="cloud_agent_script",
            height=190,
            label_visibility="collapsed",
        )
        with st.expander("View master prompt", expanded=False):
            master_prompt = st.text_area(
                "Master prompt",
                key="cloud_agent_master_prompt",
                disabled=True,
                label_visibility="collapsed",
            )
        st.caption(f"{len(script.split())} words")
        return script, master_prompt
```

Render source links with native `st.link_button` or Markdown links using the sanitized URL/title only. Put usage/cost accounting in a nested collapsed `Research details` disclosure so the main editor matches the mockup.

- [ ] **Step 5: Run Research/WebUI regressions and commit Task 4**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_webui.py \
  test/services/cloud_agent/test_research_controller.py
.venv/bin/ruff check webui/cloud_agent.py webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py
git diff --check
git add webui/cloud_agent.py webui/cloud_agent_ui.py webui/cloud_agent.css test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py
git commit -m "feat: redesign cloud agent script workspace"
```

Expected: all selected tests pass and Research safety/state assertions remain green.

---

### Task 5: Build the Generation Setup Card and Advanced Settings

**Files:**

- Modify: `webui/cloud_agent.py:854-1064`
- Modify: `webui/cloud_agent.css`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**

- Consumes: the existing Research provider/model values, TTS provider catalog/metadata, voice list, speed, defaults, API-key handlers, and prepared voice session object.
- Produces: `_GenerationSelection`, `_advanced_settings_container()`, and `_render_generation_setup(ui_state, defaults, research_settings, research_provider_catalog, script, script_mode, research_provider, research_model) -> _GenerationSelection` for the production action.

- [ ] **Step 1: Write failing Generation setup tests**

```python
class ExpanderStreamlit:
    def __init__(self):
        self.calls = []

    def expander(self, label, **kwargs):
        self.calls.append((label, kwargs))
        return nullcontext()


def test_advanced_settings_are_collapsed_behind_one_disclosure(monkeypatch):
    fake = ExpanderStreamlit()
    monkeypatch.setattr(cloud_agent, "st", fake)

    with cloud_agent._advanced_settings_container():
        pass

    assert fake.calls == [("Advanced settings", {"expanded": False})]
```

- [ ] **Step 2: Run the new test and verify RED**

```bash
.venv/bin/pytest -q test/services/test_cloud_agent_webui.py::test_advanced_settings_are_collapsed_behind_one_disclosure
```

Expected: the keyed setup card and unified Advanced settings disclosure do not exist.

- [ ] **Step 3: Move provider/voice controls into a right-column renderer**

Create the function boundaries below. Add `from dataclasses import dataclass` to `webui/cloud_agent.py` if Task 3 has not already added it.

```python
@dataclass(frozen=True)
class _GenerationSelection:
    provider: str
    voice: str
    speed: float
    prepared_voice: dict | None


def _advanced_settings_container():
    return st.expander("Advanced settings", expanded=False)


def _render_generation_setup(
    *,
    ui_state,
    defaults,
    research_settings,
    research_provider_catalog,
    script,
    script_mode,
    research_provider,
    research_model,
):
    with st.container(key="cloud_agent_generation_setup_card", border=True):
        st.subheader("Generation setup")
        if script_mode == "Research Script":
            st.caption(f"Research provider · {research_provider}")
            st.caption(f"Model · {research_model}")

        provider = st.selectbox(
            "TTS provider",
            list(provider_labels),
            format_func=lambda value: provider_labels[value],
            key="cloud_agent_provider",
            on_change=_clear_provider_feedback,
        )
        voice = st.selectbox(
            "Voice",
            list(voice_labels) or [""],
            format_func=lambda value: voice_labels.get(value, "Select a configured voice"),
            key="cloud_agent_voice",
            on_change=lambda: ui_state.pop("cloud_agent_defaults_feedback", None),
        )
        speed = st.number_input(
            "Speed",
            min_value=0.1,
            value=1.0,
            key="cloud_agent_speed",
            on_change=lambda: ui_state.pop("cloud_agent_defaults_feedback", None),
        )
        with _advanced_settings_container():
            _render_advanced_settings(
                ui_state=ui_state,
                defaults=defaults,
                research_settings=research_settings,
                research_provider_catalog=research_provider_catalog,
                provider=provider,
                provider_metadata=provider_metadata,
            )
        return _GenerationSelection(
            provider=provider,
            voice=voice,
            speed=float(speed),
            prepared_voice=prepared_voice,
        )
```

Create `_render_advanced_settings` with the six explicit keyword-only parameters shown above. Move the pre-task `Research Settings` and `Research Provider Key` blocks at lines 669–761 plus the `TTS Provider Settings`, defaults save/reset, and explicit secret-removal blocks at lines 885–1016 into this helper in that order. Preserve every existing widget key and handler, then delete the original copies so each setting renders once.

- [ ] **Step 4: Render native audio preview and Create voice action**

Inside the setup card, keep the current artifact match predicate exactly. When it matches, render:

```python
st.markdown("**Audio preview**")
st.audio(
    _prepared_voice_audio(prepared_voice["fingerprint"]),
    format="audio/mpeg",
)
```

Render `Create voice` with key `cloud_agent_create_voice`, Material audio icon, and `width="stretch"`. Keep the existing spinner, request payload, session-state fields, and safe error handling.

- [ ] **Step 5: Run voice/default/API regression tests and commit Task 5**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_webui.py \
  test/services/test_webui_startup.py \
  test/services/cloud_agent/test_research_controller.py
.venv/bin/ruff check webui/cloud_agent.py test/services/test_cloud_agent_webui.py
git diff --check
git add webui/cloud_agent.py webui/cloud_agent.css test/services/test_cloud_agent_webui.py
git commit -m "feat: redesign cloud agent generation setup"
```

Expected: all selected tests pass; secret-removal and save-verification tests remain unchanged.

---

### Task 6: Recompose the Two-Column Workspace and Production Controls

**Files:**

- Modify: `webui/cloud_agent.py:503-1150`
- Modify: `webui/cloud_agent_ui.py`
- Modify: `webui/cloud_agent.css`
- Modify: `test/services/test_cloud_agent_ui.py`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**

- Consumes: the section functions from Tasks 3–5 and the existing `_start_job`, session readiness, job GET/control APIs, and safe job error helper.
- Produces: desktop two-column composition, `Continue to production`, stored safe job snapshot, and bottom Production status card.

- [ ] **Step 1: Write failing Start-state and production-card tests**

```python
def test_successful_start_stores_job_for_production_status(monkeypatch):
    session_state = {}
    monkeypatch.setattr(cloud_agent.st, "session_state", session_state)
    monkeypatch.setattr(
        cloud_agent,
        "_start_job",
        lambda **kwargs: {
            "id": "job-123",
            "status": "QUEUED",
            "checkpoint": "NONE",
            "current_step": "queued",
            "progress": 0,
        },
    )

    cloud_agent._start_and_store_job(
        {
            "subject": "Rice",
            "target_words": 130,
            "language": "en-US",
            "script": "Ready narration",
            "master_prompt": "Ready master prompt",
            "clip_plan": {"target_words": 130, "segments": [{"index": 1}] * 6},
            "tts_provider": "elevenlabs",
            "voice_id": "voice-1",
            "voice_speed": 1.0,
            "research_draft_id": "",
            "prepared_voice_fingerprint": "",
        }
    )

    assert session_state["cloud_agent_job_id"] == "job-123"
    assert session_state["cloud_agent_job_snapshot"]["status"] == "QUEUED"


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

    cloud_agent_ui.render_production_status(stages, {"id": "job-123", "status": "QUEUED"})

    body = " ".join(fake.html_bodies)
    assert all(label in body for label in ("Script", "Voice", "Flow", "Canva", "Export"))
    assert "job-123" in body
```

- [ ] **Step 2: Run the tests and verify RED**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_ui.py::test_production_status_renders_five_fixed_safe_stages \
  test/services/test_cloud_agent_webui.py::test_successful_start_stores_job_for_production_status
```

Expected: `_start_and_store_job` and `render_production_status` are missing.

- [ ] **Step 3: Compose the approved desktop workspace**

After loading page/default/catalog state, calculate readiness and render:

```python
prepared_voice = ui_state.get("cloud_agent_prepared_voice")
script_ready = bool(str(ui_state.get("cloud_agent_draft_script") or "").strip())
prepared_voice_ready = _prepared_voice_matches(
    prepared_voice,
    script=str(ui_state.get("cloud_agent_script") or ""),
    provider=str(ui_state.get("cloud_agent_provider") or ""),
    voice=str(ui_state.get("cloud_agent_voice") or ""),
    speed=float(ui_state.get("cloud_agent_speed") or 1.0),
)
job_snapshot = dict(ui_state.get("cloud_agent_job_snapshot") or {})

cloud_agent_ui.render_workflow_rail(
    cloud_agent_ui.derive_workflow_step(
        script_ready,
        prepared_voice_ready,
        job_snapshot,
    )
)

workspace = st.columns([1.85, 1], gap="large", vertical_alignment="top")
with workspace[0]:
    brief = _render_video_brief(
        ui_state=ui_state,
        defaults=defaults,
        research_settings=research_settings,
        research_provider_catalog=research_provider_catalog,
    )
    script, master_prompt = _render_script_editor(
        brief=brief,
        ui_state=ui_state,
    )
with workspace[1]:
    generation = _render_generation_setup(
        ui_state=ui_state,
        defaults=defaults,
        research_settings=research_settings,
        research_provider_catalog=research_provider_catalog,
        script=script,
        script_mode=brief.script_mode,
        research_provider=brief.research_provider,
        research_model=brief.research_model,
    )
    _render_start_action(
        brief=brief,
        script=script,
        master_prompt=master_prompt,
        generation=generation,
        ui_state=ui_state,
    )
```

Define `_render_start_action` with the five keyword-only parameters shown above and move the pre-task Start validation/request block at lines 1075–1118 into it. Read provider, voice, speed, and prepared voice from `_GenerationSelection`; read subject, words, language, and Research selection from `_BriefSelection`. Do not call any API during layout calculation.

- [ ] **Step 4: Store the safe Start response and render the primary action**

Refactor the existing Start branch into:

```python
def _store_job_snapshot(job):
    safe_fields = (
        "id",
        "status",
        "checkpoint",
        "current_step",
        "progress",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    )
    snapshot = {name: job.get(name) for name in safe_fields if name in job}
    st.session_state["cloud_agent_job_id"] = str(snapshot.get("id") or "")
    st.session_state["cloud_agent_job_snapshot"] = snapshot


def _start_and_store_job(inputs):
    job = _start_job(**inputs)
    _store_job_snapshot(job)
    return job
```

The right-column button uses:

```python
st.button(
    "Continue to production",
    key="cloud_agent_start",
    type="primary",
    icon=":material/arrow_forward:",
    icon_position="right",
    width="stretch",
)
```

Keep the current preconditions: generated/refreshed draft must match the editor, clip plan must exist, and voice must be non-blank.

- [ ] **Step 5: Add the bottom status renderer and secondary controls**

Implement `render_production_status` with `html.escape` for job ID/status and fixed stage labels only. Place Google Flow/Canva readiness and Open Browser buttons, existing-job ID lookup, Pause, Resume, Retry, and Cancel inside a collapsed `Job controls` disclosure below the status rail. Refresh the stored snapshot after every job GET/control response with `_store_job_snapshot`.

Do not render raw `st.json(job)`. Keep `_job_error_message(job)` and show it via `st.error` when present.

- [ ] **Step 6: Run full Cloud Agent regressions and commit Task 6**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_cloud_agent_controller.py \
  test/services/cloud_agent
.venv/bin/ruff check webui/Main.py webui/cloud_agent.py webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py
git diff --check
git add webui/cloud_agent.py webui/cloud_agent_ui.py webui/cloud_agent.css test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py
git commit -m "feat: add modern cloud agent production workspace"
```

Expected: all selected tests and checks pass; no backend file is modified.

---

### Task 7: Responsive, Accessibility, and Visual Acceptance

**Files:**

- Modify: `webui/cloud_agent.css`
- Modify: `test/services/test_cloud_agent_ui.py`
- Modify: `test/services/test_webui_startup.py`
- Create: `docs/ui-reference/cloud-agent-modern-ui-implemented.png`

**Interfaces:**

- Consumes: completed keyed layout from Tasks 2–6 and the approved PNG.
- Produces: responsive/a11y CSS, a clean AppTest run, and the final implementation screenshot.

- [ ] **Step 1: Add responsive and reduced-motion rules**

Append:

```css
@media (max-width: 1100px) {
    div[data-testid="stMainBlockContainer"] { padding-inline: 1rem; }
    div[class*="st-key-cloud_agent_workspace"] div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap;
    }
    div[class*="st-key-cloud_agent_workspace"] div[data-testid="column"] {
        flex: 1 1 100% !important;
        width: 100% !important;
    }
}

@media (max-width: 760px) {
    .vt-workflow { grid-template-columns: 1fr; }
    .vt-system-status { margin-top: 2rem; }
    div[data-testid="stMainBlockContainer"] { padding: 0.9rem 0.7rem 1.5rem; }
    div[class*="st-key-cloud_agent_"] button { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
    }
}
```

- [ ] **Step 2: Add an AppTest acceptance assertion**

Extend the existing external-directory startup script to assert there is no exception and that these visible controls exist exactly once:

```python
button_labels = [item.label for item in app.button]
for required in (
    "Generate script",
    "Create voice",
    "Continue to production",
):
    if button_labels.count(required) != 1:
        raise RuntimeError(f"unexpected {required!r} count: {button_labels}")
```

Run it without a live API. The page may render safe provider-load errors, but it must not crash or attempt generation.

- [ ] **Step 3: Run the complete non-paid verification matrix**

```bash
.venv/bin/pytest -q \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_webui_startup.py \
  test/services/test_cloud_agent_controller.py \
  test/services/cloud_agent
.venv/bin/ruff check webui/Main.py webui/cloud_agent.py webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py test/services/test_webui_startup.py
.venv/bin/python -m compileall -q webui
git diff --check
git status --short
```

Expected: pytest has zero failures, Ruff passes, compileall exits 0, diff check is clean, and only intended UI/test/reference files plus the protected pre-existing config backups appear in status.

- [ ] **Step 4: Capture a deterministic desktop screenshot**

Start or reuse the deployed local WebUI/API without clicking any generation action, then run:

```bash
google-chrome \
  --headless=new \
  --no-sandbox \
  --hide-scrollbars \
  --force-device-scale-factor=1 \
  --window-size=1536,1024 \
  --virtual-time-budget=5000 \
  --screenshot=docs/ui-reference/cloud-agent-modern-ui-implemented.png \
  http://127.0.0.1:8501/
```

Expected: Chrome reports a PNG written at 1536 × 1024. Verify with:

```bash
file docs/ui-reference/cloud-agent-modern-ui-implemented.png
```

- [ ] **Step 5: Review the screenshot against the approved reference**

Open both PNGs with the image viewer and check each item:

1. wordmark/sidebar width and active Cloud Agent item;
2. header, subtitle, and Saved indicator;
3. three-step rail alignment;
4. desktop 1.85:1 column balance;
5. Video brief and Research source hierarchy;
6. Script editor status/source row;
7. Generation setup, audio preview, and primary CTA;
8. five-stage Production status card;
9. no clipping, overlap, horizontal scrollbar, secret, raw JSON, or default Streamlit red accent.

If a visual item fails, make one scoped CSS/layout adjustment, recapture, and rerun Steps 3–5. Do not change API or business logic to solve a visual mismatch.

- [ ] **Step 6: Commit visual acceptance artifacts**

```bash
git add \
  webui/cloud_agent.css \
  test/services/test_cloud_agent_ui.py \
  test/services/test_webui_startup.py \
  docs/ui-reference/cloud-agent-modern-ui-approved.png \
  docs/ui-reference/cloud-agent-modern-ui-implemented.png \
  docs/superpowers/specs/2026-08-27-cloud-agent-modern-ui-design.md \
  docs/superpowers/plans/2026-08-27-cloud-agent-modern-ui.md
git commit -m "docs: verify modern cloud agent ui"
```

- [ ] **Step 7: Request final review, push, deploy, and run safe smoke checks**

After a code/spec reviewer reports no open findings:

```bash
git push origin feature/cloud-video-agent
sudo -n systemctl restart videosturbo-webui
systemctl is-active videosturbo-api videosturbo-webui videosturbo-worker
curl -fsSI http://127.0.0.1:8501/ | head -n 1
curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/health
```

Expected: all three units are `active`, WebUI returns HTTP 200, and Cloud Agent health returns status 200 with worker/storage healthy. Do not POST draft, Research, voice, job, session-check, or browser-open endpoints during smoke verification.

---

## Plan Self-Review Result

- **Spec coverage:** All shell, workflow, brief, Research, editor, generation setup, audio, production status, responsiveness, accessibility, safety, test, screenshot, and deployment requirements map to Tasks 1–7.
- **Scope:** No backend, provider, worker, storage, browser-profile, TTS service, Research service, or database change is planned.
- **Type consistency:** `StageView`, `ResearchSummary`, `derive_workflow_step`, `build_production_stages`, `_store_job_snapshot`, and the retained widget/session keys use consistent names throughout the plan.
- **Placeholders:** Function boundaries that move existing handlers explicitly require the complete current handler body; no deferred feature or unspecified error handling remains.
