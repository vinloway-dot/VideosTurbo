# Legacy Video Generation Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire classic local video generation while preserving Cloud Agent,
Music Batch, historic output access, and all Cloud Agent production contracts.

**Architecture:** Keep the Cloud Agent router, worker and Streamlit panel as the
only VideosTurbo generation surface. Keep Music Batch as its independent page
and service family. Replace the legacy creation route with an explicit
retirement response, retain historic task reads/downloads, then remove the
legacy UI, pipeline and dependencies only after repository references reach
zero.

**Tech Stack:** Python 3.11+, FastAPI, Streamlit, pytest, Ruff, uv.

**Spec:** `docs/task15-legacy-retirement-design.md`

## Global Constraints

- Do not change Cloud Agent persistence, timing, Flow, Canva, browser-profile,
  session-control or final-validation behavior.
- Do not modify Music Batch service, GPU, UI or recovery behavior.
- Do not delete historic output files or CloudJob data.
- Do not trigger paid TTS or Flow generation in any test or smoke check.
- Every behavior change starts with a focused failing pytest assertion.
- Keep each removal category in its own commit and run Ruff plus the relevant
  retained-surface regression before its commit.

---

### Task 1: Establish retirement and retained-surface API contracts

**Files:**
- Create: `test/services/test_legacy_retirement.py`
- Modify: `app/controllers/v1/video.py`
- Modify: `test/services/test_controller_video.py`

**Interfaces:**
- Consumes: FastAPI router prefix `/api/v1`; existing historic task read and
  download handlers in `app.controllers.v1.video`.
- Produces: `POST /api/v1/videos` responds with HTTP 410 and the stable error
  code `LEGACY_VIDEO_GENERATION_RETIRED`; historic GET/read/download handlers
  retain their current behavior.

- [ ] **Step 1: Write the failing retirement route test**

```python
def test_legacy_video_creation_returns_explicit_retirement_response(client):
    response = client.post("/api/v1/videos", json={})

    assert response.status_code == 410
    assert response.json()["message"] == "LEGACY_VIDEO_GENERATION_RETIRED"
```

- [ ] **Step 2: Write the failing retained-surface guard**

```python
def test_cloud_agent_router_and_historic_task_read_handlers_remain_registered():
    source = Path("app/router.py").read_text(encoding="utf-8")
    video_source = Path("app/controllers/v1/video.py").read_text(encoding="utf-8")

    assert "cloud_agent.router" in source
    assert '"/tasks"' in video_source
    assert '"/download/{file_path:path}"' in video_source
```

- [ ] **Step 3: Verify RED evidence**

Run: `uv run pytest test/services/test_legacy_retirement.py -v`

Expected: the first test fails because `/videos` still starts classic creation,
not because the test client/import fixture is broken.

- [ ] **Step 4: Implement the smallest retirement response**

Replace only the body of the existing `create_video` handler with a 410 response
that contains `LEGACY_VIDEO_GENERATION_RETIRED`. Do not remove the controller
module or its historic task read/download handlers in this task.

- [ ] **Step 5: Verify GREEN and controller regression**

Run: `uv run pytest test/services/test_legacy_retirement.py test/services/test_controller_video.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the API contract**

```bash
git add app/controllers/v1/video.py test/services/test_legacy_retirement.py test/services/test_controller_video.py
git commit -m "feat: retire legacy video creation API"
```

### Task 2: Remove classic generation controls from the primary WebUI

**Files:**
- Modify: `webui/Main.py`
- Modify: `test/services/test_cloud_agent_webui.py`
- Modify: `test/services/test_six_clip_webui.py`
- Modify: `test/services/test_webui_generation_defaults.py`
- Delete: `webui/six_clip_timeline.py` only after Step 5 proves no retained import

**Interfaces:**
- Consumes: `webui.cloud_agent.render_cloud_agent_panel()`.
- Produces: Main WebUI renders the Cloud Agent panel and contains no classic
  Generate Video submit control, stock-material chooser, local media Upload/URL
  controls, or six-clip local timeline editor. Music Batch remains served by
  `webui/pages/2_Music_Batch.py`.

- [ ] **Step 1: Write failing Main-source contract tests**

```python
def test_main_keeps_cloud_agent_but_omits_classic_generation_controls():
    source = Path("webui/Main.py").read_text(encoding="utf-8")

    assert "cloud_agent.render_cloud_agent_panel" in source
    assert 'key="generate_video_button"' not in source
    assert "_render_six_clip_video_settings" not in source
    assert "local_video_materials_uploader" not in source
```

- [ ] **Step 2: Add a failing Music Batch retention guard**

```python
def test_music_batch_page_remains_independent_of_main():
    source = Path("webui/pages/2_Music_Batch.py").read_text(encoding="utf-8")

    assert "render_music_batch_page" in source
    assert "webui.music_batch_gpu" in source
```

- [ ] **Step 3: Verify RED evidence**

Run: `uv run pytest test/services/test_cloud_agent_webui.py test/services/test_six_clip_webui.py test/services/test_webui_generation_defaults.py -v`

Expected: the new Main-source contract fails because classic controls are still
present; existing six-clip expectations are then revised only where they assert
the retired feature.

- [ ] **Step 4: Simplify Main in one focused change**

Remove classic generation setup, submit and rendering sections together with
their now-unused imports/session-state restoration. Retain WebUI configuration,
localization, task-history rendering required for historic output access, and
the Cloud Agent panel call. Do not modify `webui/cloud_agent.py`.

- [ ] **Step 5: Prove six-clip editor has no remaining callers before deletion**

Run: `rg -n "six_clip_timeline" app webui test --glob '*.py'`

Expected: only tests that are being deleted in this task remain. Delete
`webui/six_clip_timeline.py` and those retired-feature tests only after this
command has no retained production caller.

- [ ] **Step 6: Verify GREEN and WebUI regression**

Run: `uv run pytest test/services/test_cloud_agent_webui.py test/services/test_webui_startup.py test/test_music_batch_webui.py -v`

Expected: PASS.

- [ ] **Step 7: Commit the WebUI retirement**

```bash
git add webui/Main.py webui/six_clip_timeline.py webui/pages/2_Music_Batch.py test/services test/test_music_batch_webui.py
git commit -m "feat: retire classic video generation UI"
```

### Task 3: Remove the classic task-pipeline and material-provider family

**Files:**
- Modify: `app/services/task.py`
- Modify: `app/models/schema.py`
- Modify: `app/config/config.py`
- Delete: `app/services/stock_images.py`
- Delete: `app/services/stock_materials.py`
- Delete: `app/services/image_materials.py`
- Delete: retired material/stock/six-clip tests after their production callers are removed
- Create: `test/services/test_legacy_pipeline_retirement.py`

**Interfaces:**
- Consumes: retained Cloud Agent workflow and Music Batch modules.
- Produces: no classic task submission path can download stock media, accept
  local material URLs/uploads, or invoke the local six-clip renderer; retained
  Cloud Agent and Music Batch imports stay valid.

- [ ] **Step 1: Write failing no-legacy-pipeline tests**

```python
def test_classic_task_pipeline_no_longer_imports_stock_or_local_six_clip_renderer():
    source = Path("app/services/task.py").read_text(encoding="utf-8")

    assert "stock_materials" not in source
    assert "six_clip_render" not in source

def test_cloud_agent_and_music_batch_imports_remain_available():
    from app.services.cloud_agent.workflow import CloudAgentWorkflow
    from app.services.music_batch.manager import MusicBatchManager

    assert CloudAgentWorkflow is not None
    assert MusicBatchManager is not None
```

- [ ] **Step 2: Verify RED evidence**

Run: `uv run pytest test/services/test_legacy_pipeline_retirement.py -v`

Expected: the pipeline-source assertion fails because legacy imports remain.

- [ ] **Step 3: Remove dead classic task submission branches**

Delete only branches reachable from retired video creation. Remove matching
`VideoParams` fields and configuration defaults only after no retained caller
references them. Do not remove fields used by Music Batch or Cloud Agent.

- [ ] **Step 4: Remove stock/material service modules after zero-reference proof**

Run: `rg -n "stock_images|stock_materials|image_materials" app webui test --glob '*.py'`

Expected: no retained caller. Then delete the three service modules and their
feature-only tests.

- [ ] **Step 5: Verify GREEN and retained regressions**

Run: `uv run pytest test/services/test_legacy_pipeline_retirement.py test/services/cloud_agent test/services/music_batch test/test_music_batch_webui.py -v`

Expected: PASS without paid-provider calls.

- [ ] **Step 6: Commit the pipeline retirement**

```bash
git add app/services/task.py app/models/schema.py app/config/config.py app/services/cloud_agent app/services/music_batch test/services test/test_music_batch_webui.py
git rm app/services/stock_images.py app/services/stock_materials.py app/services/image_materials.py
git commit -m "feat: remove classic stock video pipeline"
```

### Task 4: Remove the local renderer and obsolete dependencies

**Files:**
- Delete: `app/services/six_clip_render.py`
- Modify: `app/services/video.py`
- Modify: `app/services/utils/video_effects.py` only if it becomes unreachable from retained Music Batch callers
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `test/services/test_legacy_dependency_retirement.py`

**Interfaces:**
- Consumes: repository-wide zero-reference checks after Tasks 1–3.
- Produces: no classic renderer import, no MoviePy dependency unless a retained
  Music Batch path still imports it; Cloud Agent final assembly remains Canva.

- [ ] **Step 1: Write a failing static retirement test**

```python
def test_repository_has_no_classic_six_clip_renderer_reference():
    sources = [path.read_text(encoding="utf-8") for path in Path("app").rglob("*.py")]

    assert all("six_clip_render" not in source for source in sources)
```

- [ ] **Step 2: Verify RED evidence**

Run: `uv run pytest test/services/test_legacy_dependency_retirement.py -v`

Expected: FAIL because the local renderer and its import still exist before this
task's removal.

- [ ] **Step 3: Prove dependency ownership before editing**

Run: `rg -n "moviepy|from moviepy|import moviepy" app webui test --glob '*.py'`

Expected: identify every retained Music Batch caller. Do not remove MoviePy if
any retained caller remains.

- [ ] **Step 4: Remove only zero-reference renderer code and dependencies**

Delete `app/services/six_clip_render.py` after zero-reference proof. Remove
MoviePy from `pyproject.toml` only when Step 3 reports no retained caller; then
regenerate `uv.lock` with `uv lock`.

- [ ] **Step 5: Verify dependency and full regression gates**

Run: `uv lock --check && uv sync --frozen && uv run pytest test/services/cloud_agent test/services/music_batch test/services/test_controller_video.py -v && uv run ruff check app webui test`

Expected: PASS.

- [ ] **Step 6: Commit the renderer/dependency retirement**

```bash
git add pyproject.toml uv.lock app/services app/services/utils test/services
git rm app/services/six_clip_render.py
git commit -m "feat: remove classic local video renderer"
```

### Task 5: Final non-paid smoke and review preparation

**Files:**
- Create: `docs/task15-legacy-retirement-verification.md`
- Modify: `test/services/test_legacy_retirement.py`

**Interfaces:**
- Consumes: completed Task 1–4 commits.
- Produces: sanitized evidence that Cloud Agent API/WebUI/Worker remain healthy,
  both session checks are READY, Music Batch remains importable, and no paid
  provider was invoked.

- [ ] **Step 1: Add the failing evidence-structure test to the existing retirement test file**

```python
def test_retirement_verification_document_records_all_retained_surfaces():
    source = Path("docs/task15-legacy-retirement-verification.md").read_text(encoding="utf-8")

    for heading in ("Cloud Agent", "Music Batch", "API", "WebUI", "Worker", "Sessions"):
        assert heading in source
```

- [ ] **Step 2: Verify RED evidence**

Run: `uv run pytest test/services/test_legacy_retirement.py::test_retirement_verification_document_records_all_retained_surfaces -v`

Expected: FAIL because the evidence document does not yet exist.

- [ ] **Step 3: Run the non-paid runtime checks**

Run: `systemctl is-active videosturbo-api videosturbo-webui videosturbo-worker && curl --fail http://127.0.0.1:8080/api/v1/cloud-agent/health && curl --fail -X POST http://127.0.0.1:8080/api/v1/cloud-agent/sessions/check`

Expected: three active services and successful health/session responses. Record
only status values, never credentials, URLs, cookies or profile paths.

- [ ] **Step 4: Write the sanitized evidence document and verify GREEN**

Run: `uv run pytest test/services/test_legacy_retirement.py -v && uv run ruff check app webui test`

Expected: PASS.

- [ ] **Step 5: Commit final evidence**

```bash
git add docs/task15-legacy-retirement-verification.md test/services/test_legacy_retirement.py
git commit -m "test: verify legacy retirement retained surfaces"
```

## Final verification

```bash
uv run pytest test/services/cloud_agent test/services/music_batch test/services/test_controller_video.py test/services/test_cloud_agent_webui.py test/test_music_batch_webui.py -v
uv run ruff check app webui test
uv lock --check
git diff feature/cloud-video-agent...HEAD --check
```

Run the non-paid service/session smoke from Task 5. Do not run a paid Flow or
TTS job as part of this cleanup.
