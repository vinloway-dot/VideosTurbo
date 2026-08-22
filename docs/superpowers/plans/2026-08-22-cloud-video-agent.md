# Cloud Video Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent Cloud Agent that lets a user generate/review a script in VideosTurbo, press `Start Auto Production`, close their local browser/computer, and later receive a validated final MP4 produced through API TTS, Google Flow browser automation, and Canva browser automation on Ubuntu Cloud Server.

**Architecture:** Keep the current LLM/script/six-clip prompt foundation, but add a separate `app/services/cloud_agent/` domain with SQLite persistence, a single background worker, checkpointed workflow orchestration, Playwright service adapters, session preflight/auto-relogin, media validation, and cleanup. The existing FastAPI application exposes Cloud Agent control/session APIs; Streamlit becomes a thin control/status UI. Legacy render/stock code stays intact until the new End-to-End workflow passes real Ubuntu smoke testing.

**Tech Stack:** Python 3.11+, FastAPI, Streamlit, sqlite3, Playwright sync API, Chromium/Chrome, FFmpeg/ffprobe, existing `app.services.voice` TTS routing, pytest, Ruff, systemd, Xvfb/noVNC, Nginx.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md`

## Global Constraints

- Production target is Ubuntu 24.04 LTS Cloud Server, x86-64, initially 4 vCPU / 8 GB RAM / 100 GB SSD/NVMe.
- GPU is not required for MVP.
- TTS must use backend/API integration; do not automate TTS provider web pages.
- Google Flow and Canva are the only MVP browser-automation services.
- `Start Auto Production` must run Session Preflight before TTS or paid generation work.
- If a Flow/Canva service session expires but the saved Google account remains authenticated, attempt safe Auto Re-login and verify service readiness.
- Password entry, CAPTCHA, 2FA, Google Prompt, Verify-it's-you, and similar security challenges must become `HUMAN_REQUIRED`; never bypass them.
- Keep manual `Check`, `Check All Sessions`, `Open Browser`, `Check Again`, and `Resume` paths in the UI.
- Re-check Flow immediately before Flow work and Canva immediately before Canva work.
- Persist job/checkpoint state so closing the web UI or restarting the server does not lose workflow position.
- MVP runs one active worker/job at a time; additional jobs remain queued.
- Do not delete Flow source clips before Final Validation succeeds.
- Do not remove legacy stock/render paths until the new workflow passes real End-to-End Ubuntu smoke testing.
- API keys, credentials, browser profiles, and session cookies must never be committed to Git or exposed to the client.
- Existing repository convention remains Python `>=3.11`, `uv.lock`, pytest, Ruff, and at least the current 70% project coverage floor.

---

## Planned File Map

### New production modules

- `app/models/cloud_agent.py` — Cloud Agent request/record/status/session models.
- `app/services/cloud_agent/__init__.py` — package boundary.
- `app/services/cloud_agent/job_store.py` — SQLite schema, job persistence, leases and checkpoint updates.
- `app/services/cloud_agent/storage.py` — deterministic per-job directories and safe cleanup.
- `app/services/cloud_agent/media_probe.py` — ffprobe parsing and audio/video validation.
- `app/services/cloud_agent/browser.py` — persistent Playwright browser contexts/profiles and evidence screenshots.
- `app/services/cloud_agent/session.py` — session manager, Auto Re-login policy and Human Required classification.
- `app/services/cloud_agent/providers/google_flow.py` — Flow session + generation/download adapter.
- `app/services/cloud_agent/providers/canva.py` — Canva session + assembly/caption/export adapter.
- `app/services/cloud_agent/tts.py` — wrapper around existing `app.services.voice.tts`.
- `app/services/cloud_agent/workflow.py` — checkpointed production state machine.
- `app/services/cloud_agent/worker.py` — one-job-at-a-time persistent worker process.
- `app/services/cloud_agent/factory.py` — config-driven construction of real adapters for production.
- `app/controllers/v1/cloud_agent.py` — job/session control API.
- `webui/cloud_agent.py` — Streamlit rendering/API helpers for the new control/status experience.
- `deploy/systemd/videosturbo-worker.service` — background worker service.
- `deploy/systemd/videosturbo-web.service` — documented production web service unit if no equivalent exists.
- `deploy/cloud-agent/README.md` — Ubuntu + Chromium + Xvfb/noVNC deployment/runbook.

### Existing files to modify

- `app/router.py` — register Cloud Agent routes.
- `app/config/config.py` — expose normalized Cloud Agent settings.
- `config.example.toml` — document non-secret Cloud Agent configuration.
- `pyproject.toml` / `uv.lock` — add Playwright runtime dependency following the repo's exact-pin convention.
- `webui/Main.py` — integrate Cloud Agent UI while preserving current script/Master Prompt logic during migration.
- `.gitignore` — ignore browser profiles, Cloud Agent SQLite database and runtime artifacts if not already covered.

### New tests

- `test/services/cloud_agent/test_models.py`
- `test/services/cloud_agent/test_job_store.py`
- `test/services/cloud_agent/test_storage.py`
- `test/services/cloud_agent/test_media_probe.py`
- `test/services/cloud_agent/test_worker.py`
- `test/services/cloud_agent/test_session.py`
- `test/services/cloud_agent/test_tts.py`
- `test/services/cloud_agent/test_google_flow.py`
- `test/services/cloud_agent/test_canva.py`
- `test/services/cloud_agent/test_workflow.py`
- `test/services/test_cloud_agent_controller.py`
- `test/services/test_cloud_agent_webui.py`

---

### Task 1: Cloud Agent domain models and persistent SQLite job store

**Files:**
- Create: `app/models/cloud_agent.py`
- Create: `app/services/cloud_agent/__init__.py`
- Create: `app/services/cloud_agent/job_store.py`
- Test: `test/services/cloud_agent/test_models.py`
- Test: `test/services/cloud_agent/test_job_store.py`

**Interfaces:**
- Produces `CloudJobStatus`, `ServiceSessionStatus`, `CloudJobCreate`, `CloudJobRecord`, `SessionCheckResult`.
- Produces `CloudJobStore(db_path: str)` with `create_job`, `get_job`, `list_jobs`, `patch_job`, `claim_next_job`, `release_lease`.
- Later tasks must use this store instead of the legacy in-memory task state for Cloud Agent jobs.

- [ ] **Step 1: Write failing model/state tests**

```python
from app.models.cloud_agent import CloudJobStatus, ServiceSessionStatus


def test_cloud_job_states_include_preflight_and_human_required():
    assert CloudJobStatus.PREFLIGHT.value == "PREFLIGHT"
    assert CloudJobStatus.HUMAN_REQUIRED.value == "HUMAN_REQUIRED"
    assert CloudJobStatus.COMPLETED.value == "COMPLETED"


def test_session_states_separate_relogin_from_human_challenge():
    assert ServiceSessionStatus.AUTO_RELOGIN.value == "AUTO_RELOGIN"
    assert ServiceSessionStatus.CAPTCHA_REQUIRED.value == "CAPTCHA_REQUIRED"
    assert ServiceSessionStatus.READY.value == "READY"
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest test/services/cloud_agent/test_models.py -v`

Expected: FAIL because `app.models.cloud_agent` does not exist.

- [ ] **Step 3: Implement the domain models**

Define string enums for all job states in the spec and service-session states. Define Pydantic models with these exact core fields:

```python
class CloudJobCreate(BaseModel):
    subject: str
    script: str
    master_prompt: str
    language: str = ""
    target_words: int = 130
    tts_provider: str
    voice_id: str
    voice_speed: float = 1.0


class CloudJobRecord(CloudJobCreate):
    id: str
    status: CloudJobStatus
    current_step: str
    progress: int
    flow_status: str
    canva_status: str
    voice_file: str
    final_video: str
    error_code: str
    error_message: str
    created_at: str
    started_at: str
    completed_at: str
    updated_at: str
    worker_id: str
    lease_until: str
```

Use validation so `target_words` is 40–400, `voice_speed` is positive, progress is 0–100, and empty script/Master Prompt cannot start a job.

- [ ] **Step 4: Write failing SQLite persistence/lease tests**

Tests must cover:

```python
store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
job = store.create_job(request)
assert store.get_job(job.id).script == request.script

claimed = store.claim_next_job("worker-a", lease_seconds=60)
assert claimed.id == job.id
assert store.claim_next_job("worker-b", lease_seconds=60) is None

updated = store.patch_job(job.id, status=CloudJobStatus.TTS_READY, progress=20)
assert updated.status is CloudJobStatus.TTS_READY
```

Also test reopening a new `CloudJobStore` instance against the same DB returns the job, proving process persistence.

- [ ] **Step 5: Implement SQLite schema and atomic lease claiming**

Use stdlib `sqlite3`; do not add an ORM. Enable WAL mode and a busy timeout. Create a single `cloud_agent_jobs` table containing all `CloudJobRecord` fields. `claim_next_job()` must use a transaction so two workers cannot claim the same job. Claim only `QUEUED` jobs or resumable jobs whose lease is absent/expired; never claim `COMPLETED`, `FAILED`, `CANCELLED`, or `HUMAN_REQUIRED` automatically.

- [ ] **Step 6: Verify Task 1**

Run:

```bash
uv run pytest test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py -v
uv run ruff check app/models/cloud_agent.py app/services/cloud_agent/job_store.py test/services/cloud_agent
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models/cloud_agent.py app/services/cloud_agent/__init__.py app/services/cloud_agent/job_store.py test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py
git commit -m "feat: add persistent cloud agent jobs"
```

---

### Task 2: Per-job storage and FFprobe validation

**Files:**
- Create: `app/services/cloud_agent/storage.py`
- Create: `app/services/cloud_agent/media_probe.py`
- Test: `test/services/cloud_agent/test_storage.py`
- Test: `test/services/cloud_agent/test_media_probe.py`

**Interfaces:**
- `CloudJobStorage(root: Path)` produces deterministic `JobPaths`.
- `probe_media(path: Path) -> MediaProbe` uses ffprobe JSON.
- `validate_audio(path, *, min_duration, max_duration) -> MediaProbe`.
- `validate_video(path, *, min_duration=None, max_duration=None, expected_width=None, expected_height=None) -> MediaProbe`.

- [ ] **Step 1: Write RED storage tests**

Verify `prepare(job_id)` creates exactly:

```text
<input>/script.txt
<input>/master_prompt.txt
<audio>/voice.mp3
<flow>/clip_01.mp4 ... clip_06.mp4
<screenshots>/
<logs>/
<final>/final.mp4
```

Test that `job_id="../escape"` is rejected and cleanup never removes files outside the job root.

- [ ] **Step 2: Implement safe storage paths**

Base default must be `Path(utils.storage_dir("jobs", create=True))`. Accept only UUID/slug-safe job IDs. Provide:

```python
prepare(job_id) -> JobPaths
write_inputs(job_id, script, master_prompt) -> JobPaths
cleanup_flow_sources(job_id) -> None
```

`cleanup_flow_sources()` deletes only validated paths below `<job>/flow/`.

- [ ] **Step 3: Write RED ffprobe tests**

Mock `subprocess.run` and assert parser behavior for:

- valid audio-only JSON
- valid video+audio JSON
- missing stream
- corrupt/non-zero ffprobe exit
- duration outside policy
- wrong resolution

- [ ] **Step 4: Implement ffprobe wrapper**

Resolve ffprobe next to `utils.get_ffmpeg_binary()` when possible, otherwise from PATH. Call:

```text
ffprobe -v error -show_streams -show_format -of json <file>
```

Return a typed `MediaProbe` and raise a Cloud Agent validation exception with sanitized diagnostics.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_media_probe.py -v
uv run ruff check app/services/cloud_agent/storage.py app/services/cloud_agent/media_probe.py test/services/cloud_agent
```

Commit:

```bash
git add app/services/cloud_agent/storage.py app/services/cloud_agent/media_probe.py test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_media_probe.py
git commit -m "feat: add cloud agent artifact validation"
```

---

### Task 3: Single-worker queue, checkpoints, pause/resume/cancel

**Files:**
- Create: `app/services/cloud_agent/workflow.py`
- Create: `app/services/cloud_agent/worker.py`
- Test: `test/services/cloud_agent/test_worker.py`
- Test: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**
- `CloudAgentWorkflow.run(job_id: str) -> CloudJobRecord`.
- `CloudAgentWorker.run_once() -> bool` returns `True` when a job was processed/claimed.
- Worker uses `CloudJobStore` leases and never depends on an open Streamlit request.
- Workflow dependencies are injected through protocols so unit tests use fakes before real Flow/Canva adapters exist.

- [ ] **Step 1: Write RED queue tests**

Cover:

- first queued job is claimed
- second worker cannot claim active lease
- pause request leaves safe checkpoint and stops before next external step
- cancel marks `CANCELLED`
- `HUMAN_REQUIRED` is not auto-claimed
- expired worker lease can be recovered after simulated restart

- [ ] **Step 2: Implement worker polling and lease heartbeat**

Worker identity format: `hostname:pid:<uuid>`. Poll interval comes from config. `run_once()` claims at most one job and releases/updates the lease in `finally` without changing terminal states.

- [ ] **Step 3: Write RED checkpoint tests**

Use fake dependencies and assert a job whose persisted checkpoint is `FLOW_READY` resumes at Canva rather than repeating TTS/Flow. Assert retained artifact validation occurs before skipping a completed paid step.

- [ ] **Step 4: Implement checkpoint state-machine shell**

The initial workflow must support ordered named steps and terminal control semantics even before real providers are wired. Define dependency protocols:

```python
class SessionPreflight(Protocol):
    def ensure_all_ready(self, job_id: str) -> None: ...

class TTSClient(Protocol):
    def generate(self, job: CloudJobRecord, output_path: Path) -> Path: ...

class FlowClient(Protocol):
    def generate_and_download(self, job: CloudJobRecord, flow_dir: Path) -> list[Path]: ...

class CanvaClient(Protocol):
    def assemble_and_export(self, job: CloudJobRecord, clips: list[Path], audio: Path, output: Path) -> Path: ...
```

Unit-test the orchestration with fakes; production factory wiring is added later.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest test/services/cloud_agent/test_worker.py test/services/cloud_agent/test_workflow.py -v
uv run ruff check app/services/cloud_agent/workflow.py app/services/cloud_agent/worker.py test/services/cloud_agent
```

Commit:

```bash
git add app/services/cloud_agent/workflow.py app/services/cloud_agent/worker.py test/services/cloud_agent/test_worker.py test/services/cloud_agent/test_workflow.py
git commit -m "feat: add resumable cloud agent worker"
```

---

### Task 4: Playwright runtime, persistent profiles and Cloud Agent config

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `app/config/config.py`
- Modify: `config.example.toml`
- Modify: `.gitignore`
- Create: `app/services/cloud_agent/browser.py`
- Test: `test/services/cloud_agent/test_session.py` (browser config/profile tests only in this task)

**Interfaces:**
- `BrowserService` enum/string keys: `google_flow`, `canva`.
- `PersistentBrowserManager.open(service, *, headed=False)` opens a persistent Playwright context rooted in a server-side profile directory.
- `capture_evidence(service, job_id, step, page) -> Path` stores a screenshot under the job's screenshot directory.

- [ ] **Step 1: Add failing config tests**

Verify normalized defaults include:

```text
cloud_agent_enabled = false
cloud_agent_db_path = storage/cloud-agent.sqlite3
cloud_agent_worker_poll_seconds = 2
cloud_agent_max_retries = 3
cloud_agent_tts_min_duration_seconds = 58
cloud_agent_tts_max_duration_seconds = 62
cloud_agent_browser_headless = true
cloud_agent_google_profile_dir = storage/browser-profiles/google
cloud_agent_canva_profile_dir = storage/browser-profiles/canva
cloud_agent_remote_browser_url = http://127.0.0.1:6080/vnc.html
```

Paths must be server-local and never emitted with credentials.

- [ ] **Step 2: Add Playwright using the repository's exact dependency-pin convention**

Add the resolved Playwright package as an exact pin in `pyproject.toml`, regenerate `uv.lock`, and do not commit browser binaries. Installation documentation later runs `uv run playwright install --with-deps chromium` on Ubuntu.

- [ ] **Step 3: Implement persistent browser manager**

Use `playwright.sync_api.sync_playwright()` and `launch_persistent_context(user_data_dir=...)`. Do not use the operator's personal/default Chrome profile. Protect profile directories from concurrent use with one in-process lock per service; MVP has one worker.

- [ ] **Step 4: Add `.gitignore` runtime rules**

Ignore at least:

```text
storage/cloud-agent.sqlite3*
storage/browser-profiles/
storage/jobs/
```

Do not broaden ignore rules in a way that hides source fixtures/tests.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv sync --frozen
uv run pytest test/services/cloud_agent/test_session.py -v
uv run ruff check app/services/cloud_agent/browser.py app/config/config.py test/services/cloud_agent/test_session.py
```

Commit all dependency/config/runtime changes together because the browser manager requires them.

---

### Task 5: Session Preflight, Auto Re-login policy and manual recovery

**Files:**
- Create: `app/services/cloud_agent/session.py`
- Create: `app/services/cloud_agent/providers/__init__.py`
- Create: `app/services/cloud_agent/providers/google_flow.py`
- Create: `app/services/cloud_agent/providers/canva.py`
- Test: `test/services/cloud_agent/test_session.py`
- Test: `test/services/cloud_agent/test_google_flow.py`
- Test: `test/services/cloud_agent/test_canva.py`

**Interfaces:**
- Each provider implements:

```python
check_session(*, headed: bool = False) -> SessionCheckResult
repair_session(*, headed: bool = False) -> SessionCheckResult
```

- `SessionManager.check_all() -> dict[str, SessionCheckResult]`.
- `SessionManager.ensure_all_ready(job_id: str) -> dict[str, SessionCheckResult]` performs Check → Auto Re-login → Verify and raises `HumanRequiredError` only for human security challenges or unrecoverable login.

- [ ] **Step 1: Write RED classification tests**

Use fake page state rather than the live web. Required cases:

```text
authenticated service page      → READY
service login page              → SESSION_EXPIRED
Continue with Google succeeds   → READY
password challenge              → LOGIN_REQUIRED / HUMAN_REQUIRED
CAPTCHA marker                  → CAPTCHA_REQUIRED / HUMAN_REQUIRED
2FA marker                      → 2FA_REQUIRED / HUMAN_REQUIRED
Verify it's you                 → VERIFICATION_REQUIRED / HUMAN_REQUIRED
navigation/network error        → ERROR
```

- [ ] **Step 2: Implement SessionManager policy before provider-specific generation**

The manager owns policy; providers only report/check/repair their own UI. Never store a Google password in config or database. Auto-repair may click normal account-selection/Continue-with-Google UI only when an existing authenticated account is offered.

- [ ] **Step 3: Implement resilient session-only page objects**

For Google Flow and Canva, prefer Playwright role/name/URL assertions over brittle CSS nth-child selectors. Isolate service-specific locators in the provider modules. Detection must return `SessionCheckResult` with `service`, `status`, `message`, `checked_at` and optional sanitized evidence path.

- [ ] **Step 4: Add retry-bounded Preflight tests**

Assert `ensure_all_ready()`:

- checks Flow and Canva before production
- repairs one expired service and re-checks it
- stops before TTS when a human challenge remains
- never loops forever; obeys configured max retries

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest test/services/cloud_agent/test_session.py test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_canva.py -v
uv run ruff check app/services/cloud_agent/session.py app/services/cloud_agent/providers test/services/cloud_agent
```

Commit: `feat: add cloud agent session preflight`.

---

### Task 6: Cloud Agent FastAPI control and session endpoints

**Files:**
- Create: `app/controllers/v1/cloud_agent.py`
- Modify: `app/router.py`
- Test: `test/services/test_cloud_agent_controller.py`

**Interfaces:**

Under existing `/api/v1` prefix expose:

```text
POST /cloud-agent/jobs
GET  /cloud-agent/jobs/{job_id}
GET  /cloud-agent/jobs
POST /cloud-agent/jobs/{job_id}/pause
POST /cloud-agent/jobs/{job_id}/resume
POST /cloud-agent/jobs/{job_id}/cancel
GET  /cloud-agent/jobs/{job_id}/final

POST /cloud-agent/sessions/check
POST /cloud-agent/sessions/google-flow/check
POST /cloud-agent/sessions/canva/check
POST /cloud-agent/sessions/google-flow/repair
POST /cloud-agent/sessions/canva/repair
GET  /cloud-agent/sessions/{service}/open-browser
```

- [ ] **Step 1: Write RED API tests with FastAPI TestClient**

Tests must prove:

- create job persists `QUEUED`/preflight-ready data without running work inline
- GET survives new store instance
- pause/resume/cancel transitions obey allowed-state rules
- session check returns per-service status
- `open-browser` returns the configured remote browser URL only for supported services
- final endpoint rejects path traversal and returns 404 until validated final exists

- [ ] **Step 2: Implement controller using dependency factories**

Keep long-running Playwright/production work out of request handlers. Handlers only persist control requests or run bounded session checks explicitly requested by the user.

- [ ] **Step 3: Register router**

Modify `app/router.py` to include `cloud_agent.router` next to existing `video` and `llm` routers.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest test/services/test_cloud_agent_controller.py -v
uv run ruff check app/controllers/v1/cloud_agent.py app/router.py test/services/test_cloud_agent_controller.py
```

Commit: `feat: expose cloud agent control api`.

---

### Task 7: API-only TTS adapter using the existing voice service

**Files:**
- Create: `app/services/cloud_agent/tts.py`
- Test: `test/services/cloud_agent/test_tts.py`
- Reuse without browser automation: `app/services/voice.py`

**Interfaces:**

```python
class ExistingVoiceTTSClient:
    def generate(self, job: CloudJobRecord, output_path: Path) -> Path: ...
```

It calls existing:

```python
voice.tts(
    text=job.script,
    voice_name=job.voice_id,
    voice_rate=job.voice_speed,
    voice_file=str(output_path),
)
```

- [ ] **Step 1: Write RED adapter tests**

Mock `voice.tts` and prove:

- script/voice/speed/output path are passed exactly
- false/None TTS result or missing/empty output raises a typed TTS failure
- valid output is subsequently checked with `validate_audio`
- duration outside configured policy stops the workflow before Google Flow

- [ ] **Step 2: Implement TTS adapter**

Do not open ElevenLabs/Google/other TTS websites. Provider choice remains encoded by the existing `voice_id`/voice routing so existing ElevenLabs and other API-backed voices can be reused.

- [ ] **Step 3: Verify and commit**

Run:

```bash
uv run pytest test/services/cloud_agent/test_tts.py -v
uv run ruff check app/services/cloud_agent/tts.py test/services/cloud_agent/test_tts.py
```

Commit: `feat: add api tts cloud agent step`.

---

### Task 8: Google Flow generation/download adapter

**Files:**
- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Test: `test/services/cloud_agent/test_google_flow.py`
- Create: `test/resources/cloud_agent/google_flow/ready.html`
- Create: `test/resources/cloud_agent/google_flow/login.html`
- Create: `test/resources/cloud_agent/google_flow/generating.html`
- Create: `test/resources/cloud_agent/google_flow/results.html`

**Interfaces:**

```python
generate_and_download(
    job: CloudJobRecord,
    flow_dir: Path,
    expected_count: int = 6,
) -> list[Path]
```

- [ ] **Step 1: Add deterministic page-object tests**

Use local HTML fixtures to test state detection and semantic locator helpers without contacting Google in CI. Tests cover:

- Agent Mode selection
- prompt insertion
- generation-start detection
- 2/6 → 4/6 → 6/6 readiness counting
- output ordering
- failed download retry of only the failed item

- [ ] **Step 2: Implement Flow production path**

Required behavior:

```text
re-check session
→ repair/verify if needed
→ open configured Flow URL
→ enter Agent Mode
→ submit job.master_prompt
→ wait on observable result state, not fixed sleep alone
→ identify six resulting clips in chronological/result order
→ download to clip_01.mp4 ... clip_06.mp4
→ validate every clip
```

Use Playwright download events where available. If Flow exposes a signed media URL, downloading through the authenticated browser/request context is allowed, but the resulting local file is the durable artifact.

- [ ] **Step 3: Bound waiting/retry**

All waits use configured timeouts and max retries. Timeout/error captures screenshot evidence and a sanitized error message.

- [ ] **Step 4: Verify unit suite and commit**

Run:

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -v
uv run ruff check app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py
```

Commit: `feat: automate google flow video generation`.

Live Google Flow smoke is deferred to Task 13, not CI.

---

### Task 9: Canva assembly, narration, captions and export adapter

**Files:**
- Modify: `app/services/cloud_agent/providers/canva.py`
- Test: `test/services/cloud_agent/test_canva.py`
- Create: `test/resources/cloud_agent/canva/ready.html`
- Create: `test/resources/cloud_agent/canva/login.html`
- Create: `test/resources/cloud_agent/canva/editor.html`
- Create: `test/resources/cloud_agent/canva/export.html`

**Interfaces:**

```python
assemble_and_export(
    job: CloudJobRecord,
    clips: list[Path],
    audio: Path,
    output: Path,
) -> Path
```

- [ ] **Step 1: Add RED page-object tests**

Test local fixture behavior for:

- Canva ready vs login/challenge state
- upload completion tracking for six clips + one audio file
- chronological clip ordering
- narration track placement
- source-audio mute action when configured
- Auto Captions action/state
- MP4/1080p export selection
- download completion

- [ ] **Step 2: Implement Canva production path**

Required sequence:

```text
re-check session
→ repair/verify if needed
→ open configured Canva template URL (preferred MVP path)
→ upload six validated clips
→ upload voice.mp3
→ arrange 1→6 using straight cuts
→ mute generated clip audio when narration is primary
→ place narration track
→ generate Auto Captions
→ use template caption styling
→ export MP4 1080p
→ download to requested output path
```

Do not add transitions/effects in MVP unless already provided by the template.

- [ ] **Step 3: Capture evidence and bound retries**

Every unrecoverable browser failure saves a screenshot under the job evidence directory before raising a typed adapter error.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest test/services/cloud_agent/test_canva.py -v
uv run ruff check app/services/cloud_agent/providers/canva.py test/services/cloud_agent/test_canva.py
```

Commit: `feat: automate canva final assembly`.

Live Canva smoke is deferred to Task 13, not CI.

---

### Task 10: Wire the full checkpointed production workflow

**Files:**
- Modify: `app/services/cloud_agent/workflow.py`
- Create: `app/services/cloud_agent/factory.py`
- Modify: `app/services/cloud_agent/worker.py`
- Test: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**
- `build_workflow() -> CloudAgentWorkflow` constructs Store/Storage/Session/TTS/Flow/Canva/validators from config.
- `python -m app.services.cloud_agent.worker` runs the production worker loop.

- [ ] **Step 1: Write a RED full fake-E2E test**

With fake external adapters, assert exact state order:

```text
PREFLIGHT
PREFLIGHT_PASSED
TTS_GENERATING
TTS_READY
FLOW_GENERATING
FLOW_DOWNLOADING
FLOW_READY
CANVA_UPLOADING/CANVA_EDITING
CAPTIONING
EXPORTING
DOWNLOADING_FINAL
VALIDATING
COMPLETED
```

The fake test must also assert:

- Preflight runs before TTS
- Flow session re-check occurs immediately before Flow
- Canva session re-check occurs immediately before Canva
- `HUMAN_REQUIRED` stops without consuming the next step
- Resume from `FLOW_READY` skips TTS/Flow if artifacts validate
- final validation occurs before source cleanup
- failed final validation keeps source clips

- [ ] **Step 2: Implement production state wiring**

Each durable boundary writes state/checkpoint to SQLite before and after external work. On startup, worker only resumes from a checkpoint after validating artifacts required by that checkpoint.

- [ ] **Step 3: Implement bounded retry classification**

Retry transient network/browser timeouts up to configured max attempts. Human security challenges immediately become `HUMAN_REQUIRED`. Deterministic validation failures become `FAILED` with stage/error evidence.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest test/services/cloud_agent -v
uv run ruff check app/services/cloud_agent test/services/cloud_agent
```

Commit: `feat: wire cloud video production workflow`.

---

### Task 11: New VideosTurbo Cloud Agent UI

**Files:**
- Create: `webui/cloud_agent.py`
- Modify: `webui/Main.py`
- Test: `test/services/test_cloud_agent_webui.py`
- Preserve/reuse: `app/services/six_clip_plan.py`

**Interfaces:**
- UI continues to use existing script generation and six-clip Master Prompt logic.
- `webui/cloud_agent.py` owns Service Connections, Start/Status/History/Final controls rather than expanding `Main.py` further.

- [ ] **Step 1: Write RED UI structure tests**

Tests assert source/widget registration includes:

```text
Video Subject
Target Words
Language
Generate Script
View Master Prompt
TTS Provider
Voice
Speed
Check Google Flow
Check Canva
Check All Sessions
Open Browser
Start Auto Production
Pause
Resume
Cancel
Production Status
Final Video
```

Also assert legacy six-slot media upload/URL controls are not required by the Cloud Agent Start path.

- [ ] **Step 2: Implement Service Connections panel**

Render per-service status, last checked time, `Check`, `Open Browser`, and `Check All Sessions`. `Open Browser` uses only the server-configured safe remote browser URL returned by the API.

- [ ] **Step 3: Implement Start Preflight UX**

When Start is pressed, create the persistent job; do not keep the Streamlit request/page alive as the worker. UI polls job status on reruns and clearly displays `HUMAN_REQUIRED` reasons.

- [ ] **Step 4: Implement status/history/final controls**

Display progress steps and current job. Completed job shows Preview/Download. Pause/Resume/Cancel send API/state commands rather than killing arbitrary processes.

- [ ] **Step 5: Verify regressions and commit**

Run:

```bash
uv run pytest test/services/test_cloud_agent_webui.py test/services/test_six_clip_webui.py test/services/test_webui_generation_defaults.py -v
uv run ruff check webui/cloud_agent.py webui/Main.py test/services/test_cloud_agent_webui.py
```

Commit: `feat: add cloud production control ui`.

---

### Task 12: Ubuntu worker, browser and noVNC deployment

**Files:**
- Create: `deploy/systemd/videosturbo-worker.service`
- Create/update: `deploy/systemd/videosturbo-web.service`
- Create: `deploy/cloud-agent/README.md`
- Modify: `config.example.toml`

**Interfaces:**
- Web and worker run independently under systemd.
- Worker keeps running when user closes their browser.
- Xvfb/noVNC provides human access for CAPTCHA/2FA/login recovery.

- [ ] **Step 1: Add deployment runbook with exact operational sequence**

Document Ubuntu installation order:

```text
install system packages
→ install project with uv
→ uv sync --frozen
→ uv run playwright install --with-deps chromium
→ configure persistent storage/profile permissions
→ configure Xvfb/noVNC
→ configure Nginx/TLS
→ install/enable web service
→ install/enable worker service
→ first manual Google/Canva login
→ Check All Sessions
```

Do not include any real secret values.

- [ ] **Step 2: Define systemd units**

Worker command must invoke `uv run python -m app.services.cloud_agent.worker` from the project working directory with restart-on-failure. Web unit uses the project's documented production API/WebUI startup command. Both load secrets from a server-side EnvironmentFile outside Git.

- [ ] **Step 3: Document noVNC safety boundary**

noVNC must not be anonymously exposed to the public internet. Require Nginx auth/private network/VPN or equivalent deployment control. `Open Browser` points only to the configured protected URL.

- [ ] **Step 4: Commit**

Commit: `docs: add ubuntu cloud agent deployment`.

---

### Task 13: Verification gates and real Ubuntu End-to-End smoke

**Files:**
- Create: `docs/superpowers/plans/2026-08-22-cloud-video-agent-smoke-checklist.md`
- Modify tests only if the smoke exposes a reproducible bug; every fix follows RED → GREEN.

**Interfaces:**
- This is the gate before legacy cleanup or declaring the Cloud Agent production-ready.

- [ ] **Step 1: Run complete automated verification**

```bash
uv run python -m compileall app webui
uv run ruff check app webui test
uv run pytest
```

Expected: all tests pass and project coverage remains at/above configured 70% floor.

- [ ] **Step 2: Verify manual session controls on Ubuntu**

Demonstrate:

1. `Check All Sessions` reports Flow and Canva READY.
2. Force/log out only a service session while keeping Google account usable; verify Auto Re-login restores READY.
3. Trigger a human-verification/login challenge in a controlled account state; verify `HUMAN_REQUIRED`, `Open Browser`, manual resolution, `Check Again`, then READY.

Do not attempt to bypass a CAPTCHA or security challenge.

- [ ] **Step 3: Run one real paid-generation E2E job**

Required evidence:

```text
Script generated/edited
Master Prompt generated
Start pressed
Preflight passed
TTS file generated and duration validated
Google Flow created six clips
six clips downloaded and validated
Canva received six clips + narration
captions generated
MP4 exported/downloaded
final.mp4 validated
source clips cleaned only after validation
job COMPLETED
```

- [ ] **Step 4: Test server/browser independence**

Close the user's local browser after Start and verify the worker continues. Reopen VideosTurbo from another client and confirm persisted progress/final result.

- [ ] **Step 5: Test restart/resume**

After a safe checkpoint (prefer `FLOW_READY`), restart the worker/server and verify the job resumes at Canva after validating retained artifacts, without re-running TTS/Flow.

- [ ] **Step 6: Record smoke evidence and commit checklist**

The checklist records timestamps/job IDs and PASS/FAIL, but never secrets, cookies, signed URLs or private screenshots containing credentials.

---

### Task 14: Legacy cleanup — gated follow-up after Task 13 passes

**Files:**
- Determined by reference search after E2E acceptance; this task is intentionally a separate cleanup phase/PR so the working legacy baseline remains recoverable.

**Interfaces:**
- No behavior change to the accepted Cloud Agent flow.
- Remove only code proven unused by both the Cloud Agent and retained Music Batch/other explicitly kept features.

- [ ] **Step 1: Create an unused-code inventory from imports/routes/UI references**

Categorize candidates:

```text
stock providers (Pexels/Pixabay/Coverr)
legacy Main stock search/download
material type/image/mixed UI
old six-media upload/URL Main controls
Ken Burns/image processing used only by removed Main path
legacy local render components no retained feature imports
now-unused dependencies
```

Do not delete Music Batch/backend services merely because Main no longer uses them unless the product decision explicitly removes them.

- [ ] **Step 2: Remove one category at a time with tests**

For each category:

```text
write/update regression test proving retained behavior
→ remove the category
→ run focused tests
→ run full pytest + Ruff
→ commit
```

Never bundle all legacy deletion into one irreversible commit.

- [ ] **Step 3: Final dependency cleanup**

After source deletion, remove only dependencies with no runtime/test references and regenerate `uv.lock`.

- [ ] **Step 4: Final verification**

```bash
uv sync --frozen
uv run python -m compileall app webui
uv run ruff check app webui test
uv run pytest
```

Then repeat a real Cloud Agent smoke before merging cleanup.

---

## Implementation Order / Review Gates

Use these review gates rather than implementing the whole project in one uncontrolled pass:

```text
Gate A: Tasks 1–3
Persistent jobs + storage + resumable worker

Gate B: Tasks 4–6
Playwright + Session Preflight + APIs

Gate C: Tasks 7–10
TTS + Flow + Canva + full workflow

Gate D: Task 11
New UI

Gate E: Task 12
Ubuntu deployment

Gate F: Task 13
Real E2E / restart / session recovery

Gate G: Task 14
Legacy cleanup only after acceptance
```

Each gate must keep the branch testable and reviewable.

## Self-Review Against Spec

- Session Preflight before TTS: covered Tasks 5, 10, 11, 13.
- Auto Re-login and human challenge boundary: covered Tasks 5, 6, 13.
- Manual Check/Open Browser: covered Tasks 6, 11, 12, 13.
- Re-check before Flow/Canva: covered Tasks 8, 9, 10.
- API-only TTS: covered Task 7.
- Six Flow clips and selective retry: covered Task 8.
- Canva timeline/voice/captions/export: covered Task 9.
- Final validation before cleanup: covered Tasks 2, 10, 13.
- Persistence/queue/restart/resume: covered Tasks 1, 3, 10, 13.
- Ubuntu 24/7 operation: covered Task 12.
- Simplified WebUI: covered Task 11.
- Legacy code retained until E2E: enforced by Task 14 gate.
- Secrets/browser profiles excluded from Git/client: covered Tasks 4, 6, 12.

No task requires a real third-party browser session in CI; live Flow/Canva behavior is validated at the explicit Ubuntu smoke gate.
