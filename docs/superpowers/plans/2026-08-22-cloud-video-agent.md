# Cloud Video Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Before every production-code change, use `superpowers:test-driven-development`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent Cloud Agent that lets a user generate/review a script in VideosTurbo, press `Start Auto Production`, close their local browser/computer, and later receive a validated final MP4 produced through existing API-backed TTS, Google Flow browser automation, and Canva browser automation on Ubuntu Cloud Server.

**Architecture:** Reuse the current FastAPI, configuration, LLM/script generation, `SixClipPlan`, Master Prompt, TTS routing, storage-root and FFmpeg helpers. Add a focused `app/services/cloud_agent/` domain with SQLite persistence for Cloud Agent jobs/checkpoints/leases/worker heartbeat, one persistent worker, Playwright service adapters, local + session Preflight, bounded retry/resume, media validation, and cleanup. Streamlit uses the FastAPI Cloud Agent control contract; legacy render/stock paths stay intact until the real Ubuntu End-to-End gate passes.

**Tech Stack:** Python >=3.11, FastAPI, Streamlit, Pydantic, stdlib `sqlite3`, Playwright sync API, Chromium/Chrome, FFmpeg/ffprobe, existing `app.services.voice`, existing `app.services.llm`, pytest 9.1.1, Ruff 0.15.21, systemd, Xvfb/noVNC, Nginx.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md`

## Global Constraints

- Production target: Ubuntu 24.04 LTS, x86-64, initially 4 vCPU / 8 GB RAM / 100 GB SSD/NVMe.
- GPU is not required for MVP.
- Do not create a second FastAPI application; reuse `app/asgi.py`, `app/router.py`, and `app/controllers/v1/base.py::new_router()`.
- Do not replace or mutate legacy `app.services.state` / legacy task-manager behavior for Cloud Agent persistence; Cloud Agent uses its own SQLite store because restart-resume is mandatory even when Redis is disabled.
- Reuse `app.services.llm.generate_script`, `app.services.six_clip_plan`, `app.models.six_clip.SixClipPlan`, and `app.services.voice.tts` rather than duplicating provider logic.
- TTS must use backend/API integration; do not automate TTS provider web pages.
- Google Flow and Canva are the only MVP browser-automation services.
- `Start Auto Production` must run local + session Preflight before TTS or paid generation work.
- Local Preflight verifies worker heartbeat, storage writability, and configured minimum free disk.
- Session Preflight opens the real Flow/Canva services; cookie existence alone is not sufficient.
- Safe Auto Re-login may select an already-authenticated Google account; password/CAPTCHA/2FA/Google Prompt/verification challenges become `HUMAN_REQUIRED` and are never bypassed.
- Keep manual `Check`, `Check All Sessions`, `Open Browser`, `Check Again`, and `Resume` UI paths.
- Re-check Flow immediately before Flow work and Canva immediately before Canva work.
- Persist `status` separately from the last durable `checkpoint`.
- Persist the existing `SixClipPlan` as JSON; do not create a duplicate six-clip domain model.
- MVP runs one active worker/job at a time; additional jobs remain queued.
- Long external steps renew their worker lease; expired leases are recoverable only after checkpoint/artifact validation.
- Browser persistent profiles are shared across API/worker processes and require a process-safe lock, not only `threading.Lock`.
- Production human recovery uses headed Playwright on Xvfb/noVNC; headless remains a configurable development/test option.
- Do not delete Flow source clips before Final Validation succeeds.
- Do not remove legacy stock/render paths until the new workflow passes real End-to-End Ubuntu smoke testing.
- API keys, credentials, browser profiles, cookies and signed media URLs must never be committed to Git or returned to the client.
- Keep project dependency convention: exact pins in `pyproject.toml`, `uv.lock`, pytest, Ruff and the current >=70% coverage floor.

---

## Repository Facts the Plan Must Preserve

Verified on `feature/cloud-video-agent` before implementation:

- `app/asgi.py` already creates the FastAPI app and includes `root_api_router`.
- `app/controllers/v1/base.py::new_router()` applies `/api/v1`.
- `app/services/state.py` provides `MemoryState` / optional `RedisState`, but default MemoryState is not restart-durable.
- `app/controllers/manager/*` is coupled to the legacy video task flow and is not reused as the Cloud Agent durable queue.
- `app/services/voice.py::tts(text, voice_name, voice_rate, voice_file, voice_volume=1.0)` already routes multiple API-backed TTS providers including ElevenLabs.
- `app/services/llm.py::generate_script(...)` already centralizes LLM provider behavior.
- `app/services/six_clip_plan.py` and `app/models/six_clip.py` already own the six fixed 10-second segments and Master Prompt construction.
- `app/utils/utils.py::storage_dir(...)` and `get_ffmpeg_binary()` already exist.
- `deploy/systemd/videosturbo-webui.service.example` already runs Streamlit; update/reuse it rather than creating a duplicate WebUI service.
- `main.py` starts the existing FastAPI app with Uvicorn; production deployment therefore needs a separate API systemd service in addition to WebUI and worker.

---

## Planned File Map

### New production modules

- `app/models/cloud_agent.py` — Cloud Agent status/control/request/record/session/health models.
- `app/services/cloud_agent/__init__.py` — package boundary.
- `app/services/cloud_agent/errors.py` — typed workflow/session/validation errors.
- `app/services/cloud_agent/job_store.py` — SQLite schema, job persistence, atomic claiming, lease renewal and worker heartbeat.
- `app/services/cloud_agent/storage.py` — deterministic per-job directories, input writing and safe cleanup.
- `app/services/cloud_agent/media_probe.py` — ffprobe parsing and audio/video validation.
- `app/services/cloud_agent/browser_lock.py` — process-safe per-service profile locking.
- `app/services/cloud_agent/browser.py` — persistent Playwright contexts/profiles and evidence capture.
- `app/services/cloud_agent/preflight.py` — local worker/storage checks plus session-preflight orchestration.
- `app/services/cloud_agent/session.py` — Flow/Canva session policy and safe Auto Re-login classification.
- `app/services/cloud_agent/providers/__init__.py` — provider package.
- `app/services/cloud_agent/providers/google_flow.py` — Google Flow adapter.
- `app/services/cloud_agent/providers/canva.py` — Canva adapter.
- `app/services/cloud_agent/tts.py` — thin adapter around existing `app.services.voice.tts`.
- `app/services/cloud_agent/workflow.py` — durable checkpointed production state machine.
- `app/services/cloud_agent/worker.py` — persistent one-job-at-a-time worker loop.
- `app/services/cloud_agent/factory.py` — config-driven production construction.
- `app/controllers/v1/cloud_agent.py` — Cloud Agent API.
- `webui/cloud_agent.py` — Streamlit Cloud Agent API/UI helpers.
- `deploy/systemd/videosturbo-api.service.example` — existing FastAPI/Uvicorn service.
- `deploy/systemd/videosturbo-worker.service.example` — Cloud Agent worker service.
- `deploy/cloud-agent/README.md` — Ubuntu/Xvfb/noVNC/Nginx runbook.

### Existing files to modify

- `app/router.py` — register Cloud Agent controller.
- `app/config/config.py` — expose/reuse `[app]` Cloud Agent settings only if normalization helpers are needed.
- `config.example.toml` — document non-secret Cloud Agent settings under `[app]`.
- `pyproject.toml` / `uv.lock` — exact-pin Playwright dependency.
- `webui/Main.py` — integrate Cloud Agent UI while preserving current script/six-clip generation.
- `.gitignore` — ignore Cloud Agent DB, job artifacts and local browser profiles.
- `deploy/systemd/videosturbo-webui.service.example` — retain current Streamlit startup and align environment/reverse-proxy expectations.

### New tests

- `test/services/cloud_agent/test_models.py`
- `test/services/cloud_agent/test_job_store.py`
- `test/services/cloud_agent/test_storage.py`
- `test/services/cloud_agent/test_media_probe.py`
- `test/services/cloud_agent/test_browser_lock.py`
- `test/services/cloud_agent/test_browser.py`
- `test/services/cloud_agent/test_preflight.py`
- `test/services/cloud_agent/test_session.py`
- `test/services/cloud_agent/test_tts.py`
- `test/services/cloud_agent/test_google_flow.py`
- `test/services/cloud_agent/test_canva.py`
- `test/services/cloud_agent/test_worker.py`
- `test/services/cloud_agent/test_workflow.py`
- `test/services/test_cloud_agent_controller.py`
- `test/services/test_cloud_agent_webui.py`
- local HTML fixtures under `test/resources/cloud_agent/{google_flow,canva}/`.

---

# Gate A — Durable jobs, artifacts and worker semantics

### Task 1: Cloud Agent domain models + SQLite job/checkpoint store

**Files:**
- Create: `app/models/cloud_agent.py`
- Create: `app/services/cloud_agent/__init__.py`
- Create: `app/services/cloud_agent/errors.py`
- Create: `app/services/cloud_agent/job_store.py`
- Test: `test/services/cloud_agent/test_models.py`
- Test: `test/services/cloud_agent/test_job_store.py`

**Interfaces:**

- Reuse `app.models.six_clip.SixClipPlan` for `clip_plan` input and JSON persistence.
- Produce `CloudJobStatus`, `CloudJobCheckpoint`, `CloudControlRequest`, `ServiceSessionStatus`.
- Produce `CloudJobCreate`, `CloudJobRecord`, `SessionCheckResult`, `CloudAgentHealth`.
- Produce `CloudJobStore(db_path: str)` with:

```python
create_job(request: CloudJobCreate) -> CloudJobRecord
get_job(job_id: str) -> CloudJobRecord | None
list_jobs(limit: int = 50, offset: int = 0) -> list[CloudJobRecord]
patch_job(job_id: str, **changes) -> CloudJobRecord
claim_next_job(worker_id: str, lease_seconds: int) -> CloudJobRecord | None
renew_lease(job_id: str, worker_id: str, lease_seconds: int) -> bool
release_lease(job_id: str, worker_id: str) -> bool
update_worker_heartbeat(worker_id: str, *, now: str | None = None) -> None
get_worker_last_seen(worker_id: str | None = None) -> str | None
```

- Cloud Agent code never writes its durable state through legacy `sm.state`.

- [ ] **Step 1: Write failing enum/model tests**

```python
from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobStatus,
    ServiceSessionStatus,
)


def test_status_and_checkpoint_are_separate_domains():
    assert CloudJobStatus.HUMAN_REQUIRED.value == "HUMAN_REQUIRED"
    assert CloudJobStatus.PAUSED.value == "PAUSED"
    assert CloudJobCheckpoint.FLOW_READY.value == "FLOW_READY"
    assert CloudJobCheckpoint.FINAL_VALIDATED.value == "FINAL_VALIDATED"


def test_control_request_is_not_encoded_as_status():
    assert CloudControlRequest.NONE.value == "NONE"
    assert CloudControlRequest.PAUSE.value == "PAUSE"
    assert CloudControlRequest.CANCEL.value == "CANCEL"


def test_session_states_include_safe_recovery_and_human_challenges():
    assert ServiceSessionStatus.AUTO_RELOGIN.value == "AUTO_RELOGIN"
    assert ServiceSessionStatus.CAPTCHA_REQUIRED.value == "CAPTCHA_REQUIRED"
    assert ServiceSessionStatus.READY.value == "READY"
```

- [ ] **Step 2: Run focused test and verify RED**

Run:

```bash
uv run pytest test/services/cloud_agent/test_models.py -v
```

Expected: FAIL because `app.models.cloud_agent` does not exist.

- [ ] **Step 3: Implement minimal domain models**

Use Pydantic and existing `SixClipPlan`:

```python
class CloudJobCreate(BaseModel):
    subject: str
    script: str
    master_prompt: str
    clip_plan: SixClipPlan
    language: str = ""
    target_words: int = Field(default=130, ge=40, le=400)
    tts_provider: str
    voice_id: str
    voice_speed: float = Field(default=1.0, gt=0)


class CloudJobRecord(CloudJobCreate):
    id: str
    status: CloudJobStatus
    checkpoint: CloudJobCheckpoint
    control_request: CloudControlRequest
    current_step: str
    progress: int = Field(ge=0, le=100)
    flow_status: str
    canva_status: str
    voice_file: str
    final_video: str
    error_code: str
    error_message: str
    worker_id: str
    lease_until: str
    created_at: str
    started_at: str
    completed_at: str
    updated_at: str
```

Validate non-empty script/Master Prompt, and validate `target_words == clip_plan.target_words` so stored content cannot silently disagree.

- [ ] **Step 4: Write failing persistence, claim, lease and heartbeat tests**

```python
store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
job = store.create_job(request)
assert store.get_job(job.id).clip_plan == request.clip_plan

claimed = store.claim_next_job("worker-a", lease_seconds=60)
assert claimed.id == job.id
assert store.claim_next_job("worker-b", lease_seconds=60) is None

assert store.renew_lease(job.id, "worker-a", lease_seconds=60) is True
assert store.renew_lease(job.id, "worker-b", lease_seconds=60) is False

store.update_worker_heartbeat("worker-a", now="2026-08-22T06:00:00+00:00")
assert store.get_worker_last_seen("worker-a") == "2026-08-22T06:00:00+00:00"
```

Also test:

- a new store instance sees the same job;
- `PAUSED`, `HUMAN_REQUIRED`, `FAILED`, `CANCELLED`, `COMPLETED` are never auto-claimed;
- expired lease can be reclaimed only for resumable active/queued work;
- `release_lease` fails for the wrong worker;
- `clip_plan_json` round-trips through `SixClipPlan.model_validate_json(...)`.

- [ ] **Step 5: Run persistence tests and verify RED**

```bash
uv run pytest test/services/cloud_agent/test_job_store.py -v
```

Expected: FAIL because `CloudJobStore` is missing.

- [ ] **Step 6: Implement SQLite schema and atomic transactions**

Use stdlib `sqlite3`; no ORM. Enable WAL and busy timeout. Use a `cloud_agent_jobs` table plus a small `cloud_agent_workers` heartbeat table. Store `clip_plan_json` as JSON text from `SixClipPlan.model_dump_json()`.

`claim_next_job()` must use a transaction (`BEGIN IMMEDIATE` or equivalent safe sequence) so two processes cannot claim the same row.

- [ ] **Step 7: Verify GREEN and regressions**

```bash
uv run pytest test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py -v
uv run ruff check app/models/cloud_agent.py app/services/cloud_agent/errors.py app/services/cloud_agent/job_store.py test/services/cloud_agent
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/models/cloud_agent.py app/services/cloud_agent/__init__.py app/services/cloud_agent/errors.py app/services/cloud_agent/job_store.py test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py
git commit -m "feat: add persistent cloud agent jobs"
```

---

### Task 2: Per-job storage + FFprobe media validation

**Files:**
- Create: `app/services/cloud_agent/storage.py`
- Create: `app/services/cloud_agent/media_probe.py`
- Test: `test/services/cloud_agent/test_storage.py`
- Test: `test/services/cloud_agent/test_media_probe.py`

**Interfaces:**

```python
CloudJobStorage(root: Path | None = None)
prepare(job_id: str) -> JobPaths
write_inputs(job_id: str, script: str, master_prompt: str) -> JobPaths
cleanup_flow_sources(job_id: str) -> None

probe_media(path: Path) -> MediaProbe
validate_audio(path: Path, *, min_duration: float, max_duration: float) -> MediaProbe
validate_video(
    path: Path,
    *,
    require_audio: bool = False,
    min_size_bytes: int = 1,
    min_duration: float | None = None,
    max_duration: float | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> MediaProbe
```

Default storage root is `Path(utils.storage_dir("jobs", create=True))`.

- [ ] **Step 1: Write failing storage tests**

```python
paths = CloudJobStorage(tmp_path).prepare("job-123")
assert paths.input_dir == tmp_path / "job-123" / "input"
assert paths.voice_file == tmp_path / "job-123" / "audio" / "voice.mp3"
assert paths.flow_files[0].name == "clip_01.mp4"
assert paths.flow_files[-1].name == "clip_06.mp4"
assert paths.final_file.name == "final.mp4"
assert not paths.voice_file.exists()
assert not paths.final_file.exists()
```

Also reject `../escape`, absolute paths, separators, and prove cleanup cannot remove outside `<job>/flow/`.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/cloud_agent/test_storage.py -v
```

- [ ] **Step 3: Implement safe storage paths**

`prepare()` creates directories only. `write_inputs()` writes UTF-8 script and Master Prompt. `cleanup_flow_sources()` resolves every candidate below the job's `flow/` directory before deletion.

- [ ] **Step 4: Write failing ffprobe parser/validation tests**

Patch `subprocess.run` with representative JSON for:

```text
valid audio-only
valid video-only Flow clip
valid final video+audio
missing audio
missing video
non-zero ffprobe exit
invalid JSON
file below minimum size
out-of-policy duration
wrong resolution
```

- [ ] **Step 5: Implement ffprobe wrapper**

Resolve ffprobe beside `utils.get_ffmpeg_binary()` when the resolved binary is an absolute/local path; otherwise use `shutil.which("ffprobe")` then `"ffprobe"`.

Command:

```text
ffprobe -v error -show_streams -show_format -of json <file>
```

Normalize streams/duration into `MediaProbe`. Raise typed `MediaValidationError` with sanitized diagnostics.

- [ ] **Step 6: Verify GREEN and commit**

```bash
uv run pytest test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_media_probe.py -v
uv run ruff check app/services/cloud_agent/storage.py app/services/cloud_agent/media_probe.py test/services/cloud_agent

git add app/services/cloud_agent/storage.py app/services/cloud_agent/media_probe.py test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_media_probe.py
git commit -m "feat: add cloud agent artifact validation"
```

---

### Task 3: Worker queue, control requests, checkpoint resume + lease renewal

**Files:**
- Create: `app/services/cloud_agent/workflow.py`
- Create: `app/services/cloud_agent/worker.py`
- Test: `test/services/cloud_agent/test_worker.py`
- Test: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**

```python
CloudAgentWorkflow.run(job_id: str, *, worker_id: str) -> CloudJobRecord
CloudAgentWorker.run_once() -> bool
CloudAgentWorker.run_forever() -> None
```

Initial dependency protocols:

```python
class PreflightClient(Protocol):
    def ensure_ready(self, job_id: str) -> None: ...

class TTSClient(Protocol):
    def generate(self, job: CloudJobRecord, output_path: Path) -> Path: ...

class FlowClient(Protocol):
    def generate_and_download(self, job: CloudJobRecord, flow_dir: Path) -> list[Path]: ...

class CanvaClient(Protocol):
    def assemble_and_export(
        self,
        job: CloudJobRecord,
        clips: list[Path],
        audio: Path,
        output: Path,
    ) -> Path: ...
```

- [ ] **Step 1: Write failing worker tests**

Cover:

```python
assert worker.run_once() is True
assert second_worker.run_once() is False
```

and:

- heartbeat is updated even when no job is available;
- worker renews the job lease during a simulated long step;
- wrong-worker lease cannot be renewed/released;
- expired lease is recoverable after restart;
- `PAUSED` / `HUMAN_REQUIRED` are not auto-claimed.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/cloud_agent/test_worker.py -v
```

- [ ] **Step 3: Implement minimal worker loop and lease-heartbeat helper**

Worker ID:

```python
f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
```

A background lease-renewal helper is allowed only after the RED tests exist. It renews at an interval safely shorter than the configured lease and stops in `finally`.

- [ ] **Step 4: Write failing checkpoint/control tests**

Use fake dependencies to assert:

```text
checkpoint=FLOW_READY + valid voice/clips
→ does not call TTS
→ does not call Flow
→ calls Canva next
```

Also assert:

- `control_request=PAUSE` at a safe boundary sets `PAUSED` while preserving checkpoint;
- `control_request=CANCEL` sets `CANCELLED` and stops before the next external step;
- `HUMAN_REQUIRED` preserves checkpoint;
- invalid artifact required by a checkpoint prevents silently skipping the paid step.

- [ ] **Step 5: Implement state-machine shell**

Keep status and checkpoint separate. Do not add generic `RUNNING`. The shell may use fake/no-op external clients until later tasks supply production adapters.

- [ ] **Step 6: Verify GREEN and commit**

```bash
uv run pytest test/services/cloud_agent/test_worker.py test/services/cloud_agent/test_workflow.py -v
uv run ruff check app/services/cloud_agent/workflow.py app/services/cloud_agent/worker.py test/services/cloud_agent

git add app/services/cloud_agent/workflow.py app/services/cloud_agent/worker.py test/services/cloud_agent/test_worker.py test/services/cloud_agent/test_workflow.py
git commit -m "feat: add resumable cloud agent worker"
```

---

# Gate B — Browser runtime, Preflight and FastAPI contract

### Task 4: Cloud Agent config + Playwright + process-safe profile locking

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `config.example.toml`
- Modify: `.gitignore`
- Create: `app/services/cloud_agent/browser_lock.py`
- Create: `app/services/cloud_agent/browser.py`
- Test: `test/services/cloud_agent/test_browser_lock.py`
- Test: `test/services/cloud_agent/test_browser.py`
- Extend: `test/services/test_config.py`

**Interfaces:**

```python
BrowserService = Literal["google_flow", "canva"]
ProfileLock.acquire(service: str, *, timeout_seconds: float) -> ContextManager[None]
PersistentBrowserManager.open(service: str, *, headed: bool | None = None)
PersistentBrowserManager.capture_evidence(...)
```

Config under existing `[app]` includes:

```text
cloud_agent_enabled = false
cloud_agent_db_path = storage/cloud-agent.sqlite3
cloud_agent_worker_poll_seconds = 2
cloud_agent_worker_lease_seconds = 120
cloud_agent_worker_heartbeat_seconds = 10
cloud_agent_max_retries = 3
cloud_agent_min_free_disk_gb = 10
cloud_agent_tts_min_duration_seconds = 58
cloud_agent_tts_max_duration_seconds = 62
cloud_agent_final_min_size_bytes = 1048576
cloud_agent_expected_width = 1080
cloud_agent_expected_height = 1920
cloud_agent_browser_headless = true
cloud_agent_google_profile_dir = storage/browser-profiles/google-flow
cloud_agent_canva_profile_dir = storage/browser-profiles/canva
cloud_agent_browser_lock_dir = storage/browser-locks
cloud_agent_remote_browser_url = http://127.0.0.1:6080/vnc.html
cloud_agent_flow_url = ""
cloud_agent_canva_template_url = ""
```

Production may override profile paths to `/var/lib/videosturbo/...` and uses `cloud_agent_browser_headless=false` with Xvfb.

- [ ] **Step 1: Write failing config tests**

Assert missing settings receive documented defaults without creating a second config loader. Assert profile/DB paths are strings and no real credential values are emitted.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/test_config.py -k cloud_agent -v
```

- [ ] **Step 3: Add exact Playwright pin and config entries**

Follow current `pyproject.toml` exact-pin style and regenerate `uv.lock`. Do not commit Playwright browser binaries.

- [ ] **Step 4: Write failing cross-process lock tests**

Use `multiprocessing` with a temporary lock directory. Process A holds `google_flow`; Process B must time out instead of opening the same profile. A `canva` lock remains independent.

- [ ] **Step 5: Implement process-safe profile lock**

Use an OS file-lock implementation appropriate to supported platforms; keep the API isolated in `browser_lock.py`. On Ubuntu, the lock must be advisory-process-safe. Do not rely only on `threading.Lock`.

- [ ] **Step 6: Write failing browser-manager tests**

Patch Playwright and assert:

- correct service profile path;
- `launch_persistent_context(user_data_dir=...)` is used;
- configured `headless` is honored;
- profile lock is acquired before launch;
- evidence path is job-owned;
- unsupported service is rejected.

- [ ] **Step 7: Implement browser manager**

Use `playwright.sync_api.sync_playwright()`. Never use the operator's personal/default Chrome profile.

- [ ] **Step 8: Add runtime ignore rules**

At minimum:

```text
storage/cloud-agent.sqlite3*
storage/browser-profiles/
storage/browser-locks/
storage/jobs/
```

Do not hide test fixtures.

- [ ] **Step 9: Verify GREEN and commit**

```bash
uv sync --frozen
uv run pytest test/services/test_config.py -k cloud_agent -v
uv run pytest test/services/cloud_agent/test_browser_lock.py test/services/cloud_agent/test_browser.py -v
uv run ruff check app/services/cloud_agent/browser_lock.py app/services/cloud_agent/browser.py test/services/cloud_agent

git add pyproject.toml uv.lock config.example.toml .gitignore app/services/cloud_agent/browser_lock.py app/services/cloud_agent/browser.py test/services/test_config.py test/services/cloud_agent/test_browser_lock.py test/services/cloud_agent/test_browser.py
git commit -m "feat: add cloud agent browser runtime"
```

---

### Task 5: Session policy + local/session Preflight

**Files:**
- Create: `app/services/cloud_agent/preflight.py`
- Create: `app/services/cloud_agent/session.py`
- Create: `app/services/cloud_agent/providers/__init__.py`
- Create: `app/services/cloud_agent/providers/google_flow.py`
- Create: `app/services/cloud_agent/providers/canva.py`
- Test: `test/services/cloud_agent/test_preflight.py`
- Test: `test/services/cloud_agent/test_session.py`
- Test: `test/services/cloud_agent/test_google_flow.py`
- Test: `test/services/cloud_agent/test_canva.py`

**Interfaces:**

Provider session contract:

```python
check_session(*, headed: bool = False) -> SessionCheckResult
repair_session(*, headed: bool = False) -> SessionCheckResult
```

Policy:

```python
SessionManager.check_all() -> dict[str, SessionCheckResult]
SessionManager.ensure_service_ready(service: str, job_id: str) -> SessionCheckResult
SessionManager.ensure_all_ready(job_id: str) -> dict[str, SessionCheckResult]

PreflightManager.ensure_ready(job_id: str, worker_id: str) -> PreflightResult
```

- [ ] **Step 1: Write failing service-session classification tests**

Use deterministic fake/local page states:

```text
authenticated service page      → READY
service login page              → SESSION_EXPIRED
Continue with Google succeeds   → READY
password challenge              → LOGIN_REQUIRED / HumanRequiredError
CAPTCHA marker                  → CAPTCHA_REQUIRED / HumanRequiredError
2FA marker                      → 2FA_REQUIRED / HumanRequiredError
Verify it's you                 → VERIFICATION_REQUIRED / HumanRequiredError
network/navigation error        → ERROR
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/cloud_agent/test_session.py -v
```

- [ ] **Step 3: Implement SessionManager policy first**

The manager owns Check → safe repair → Verify. Providers own only service-specific page detection/actions. Never put a Google password in config/SQLite.

- [ ] **Step 4: Write failing local Preflight tests**

Patch `shutil.disk_usage` and store heartbeat:

```python
result = preflight.ensure_ready("job-1", "worker-a")
assert result.storage_ready is True
assert result.worker_ready is True
```

Required failures:

- storage root cannot be created/written;
- free disk below configured minimum;
- worker heartbeat/identity not current;
- Flow not ready after bounded safe repair;
- Canva challenge becomes `HUMAN_REQUIRED` before TTS is called.

- [ ] **Step 5: Implement `PreflightManager`**

Order:

```text
verify executing worker identity/heartbeat
→ verify storage writable + free-space threshold
→ SessionManager.ensure_all_ready(job_id)
```

- [ ] **Step 6: Add bounded retry tests**

Assert no infinite loops and max retries are configuration-driven.

- [ ] **Step 7: Implement initial session-only page objects**

Prefer Playwright roles/names/URL checks over brittle positional selectors. Keep all service-specific locators in provider modules.

- [ ] **Step 8: Verify GREEN and commit**

```bash
uv run pytest test/services/cloud_agent/test_preflight.py test/services/cloud_agent/test_session.py test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_canva.py -v
uv run ruff check app/services/cloud_agent/preflight.py app/services/cloud_agent/session.py app/services/cloud_agent/providers test/services/cloud_agent

git add app/services/cloud_agent/preflight.py app/services/cloud_agent/session.py app/services/cloud_agent/providers test/services/cloud_agent/test_preflight.py test/services/cloud_agent/test_session.py test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_canva.py
git commit -m "feat: add cloud agent preflight"
```

---

### Task 6: FastAPI Cloud Agent control/session API

**Files:**
- Create: `app/controllers/v1/cloud_agent.py`
- Modify: `app/router.py`
- Test: `test/services/test_cloud_agent_controller.py`

**Interfaces:**

Effective routes under existing `/api/v1`:

```text
GET  /cloud-agent/health
POST /cloud-agent/jobs
GET  /cloud-agent/jobs
GET  /cloud-agent/jobs/{job_id}
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

- [ ] **Step 1: Write failing API tests with `TestClient`**

Required behavior:

```python
response = client.post("/api/v1/cloud-agent/jobs", json=payload)
assert response.status_code == 200
assert response.json()["data"]["status"] == "QUEUED"
```

Also test:

- job payload requires complete existing `SixClipPlan` + Master Prompt;
- create does not execute TTS/Playwright inline;
- GET survives a new store instance;
- pause sets control request and returns observable state according to transition rules;
- resume validates permitted source state before requeue;
- cancel requests cancellation without killing arbitrary processes;
- health reports worker last-seen and storage status;
- manual session checks respect profile-lock busy response;
- `open-browser` only returns the configured protected URL for supported services;
- final endpoint serves only a job-owned `FINAL_VALIDATED` path and rejects traversal/nonexistent files.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/test_cloud_agent_controller.py -v
```

- [ ] **Step 3: Implement controller with dependency factories**

Use `new_router()`, `utils.get_response(...)`, repository error conventions and safe `FileResponse`. Keep normal job work outside request handlers.

- [ ] **Step 4: Register router**

```python
from app.controllers.v1 import cloud_agent, llm, video

root_api_router.include_router(video.router)
root_api_router.include_router(llm.router)
root_api_router.include_router(cloud_agent.router)
```

- [ ] **Step 5: Verify GREEN and commit**

```bash
uv run pytest test/services/test_cloud_agent_controller.py test/services/test_controller_base.py -v
uv run ruff check app/controllers/v1/cloud_agent.py app/router.py test/services/test_cloud_agent_controller.py

git add app/controllers/v1/cloud_agent.py app/router.py test/services/test_cloud_agent_controller.py
git commit -m "feat: expose cloud agent api"
```

---

# Gate C — TTS, Flow, Canva and full workflow

### Task 7: Existing TTS adapter + duration gate

**Files:**
- Create: `app/services/cloud_agent/tts.py`
- Test: `test/services/cloud_agent/test_tts.py`
- Reuse unchanged: `app/services/voice.py`

**Interfaces:**

```python
class ExistingVoiceTTSClient:
    def generate(self, job: CloudJobRecord, output_path: Path) -> Path: ...
```

Calls exactly:

```python
voice.tts(
    text=job.script,
    voice_name=job.voice_id,
    voice_rate=job.voice_speed,
    voice_file=str(output_path),
)
```

- [ ] **Step 1: Write failing adapter tests**

Assert exact arguments and these failures:

- `voice.tts` returns `None`;
- file missing;
- file empty;
- audio validator fails;
- duration outside configured min/max;
- inconsistent `tts_provider`/`voice_id` metadata is rejected by the adapter's lightweight validation rather than introducing a new provider registry.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/cloud_agent/test_tts.py -v
```

- [ ] **Step 3: Implement minimal adapter**

Call existing TTS routing and then `validate_audio(...)`. Do not duplicate provider HTTP clients.

- [ ] **Step 4: Verify GREEN and commit**

```bash
uv run pytest test/services/cloud_agent/test_tts.py test/services/test_voice.py -v
uv run ruff check app/services/cloud_agent/tts.py test/services/cloud_agent/test_tts.py

git add app/services/cloud_agent/tts.py test/services/cloud_agent/test_tts.py
git commit -m "feat: add cloud agent tts step"
```

---

### Task 8: Google Flow generation + selective download retry

**Files:**
- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Test: `test/services/cloud_agent/test_google_flow.py`
- Create fixtures:
  - `test/resources/cloud_agent/google_flow/ready.html`
  - `test/resources/cloud_agent/google_flow/login.html`
  - `test/resources/cloud_agent/google_flow/challenge.html`
  - `test/resources/cloud_agent/google_flow/generating.html`
  - `test/resources/cloud_agent/google_flow/results.html`

**Interfaces:**

```python
generate_and_download(
    job: CloudJobRecord,
    flow_dir: Path,
    expected_count: int = 6,
) -> list[Path]
```

- [ ] **Step 1: Write failing deterministic page-object tests**

Cover:

- Agent Mode selection;
- Master Prompt insertion;
- generation-start detection;
- observable 2/6 → 4/6 → 6/6 readiness;
- chronological/result ordering;
- bounded wait timeout;
- download naming `clip_01.mp4` … `clip_06.mp4`;
- retry only failed item/download;
- validation failure for one clip does not silently mark `FLOW_READY`.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -v
```

- [ ] **Step 3: Implement production path**

Sequence:

```text
SessionManager.ensure_service_ready("google_flow")
→ open configured Flow URL
→ enter Agent Mode
→ submit job.master_prompt
→ observe result state, not fixed sleep alone
→ collect six result items in stable order
→ download each to deterministic path
→ validate every file
```

Use Playwright download events or authenticated browser/request context when Flow exposes a signed media URL. Never log/persist signed URLs.

- [ ] **Step 4: Add evidence + bounded retry**

On unrecoverable browser failure, capture sanitized URL/error and safe screenshot. Use configured timeout/max retries.

- [ ] **Step 5: Verify GREEN and commit**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -v
uv run ruff check app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py

git add app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py test/resources/cloud_agent/google_flow
git commit -m "feat: automate google flow generation"
```

Live Google Flow verification is Task 13, not CI.

---

### Task 9: Canva template assembly, narration, captions + export

**Files:**
- Modify: `app/services/cloud_agent/providers/canva.py`
- Test: `test/services/cloud_agent/test_canva.py`
- Create fixtures:
  - `test/resources/cloud_agent/canva/ready.html`
  - `test/resources/cloud_agent/canva/login.html`
  - `test/resources/cloud_agent/canva/challenge.html`
  - `test/resources/cloud_agent/canva/editor.html`
  - `test/resources/cloud_agent/canva/export.html`

**Interfaces:**

```python
assemble_and_export(
    job: CloudJobRecord,
    clips: list[Path],
    audio: Path,
    output: Path,
) -> Path
```

- [ ] **Step 1: Write failing page-object tests**

Cover:

- ready/login/challenge state;
- configured template URL required for MVP production path;
- six clips + one audio upload completion;
- clip ordering 1→6;
- narration placement;
- source-audio mute when configured;
- Auto Captions action/ready state;
- MP4 + 1080p export selection;
- final download completion;
- screenshot evidence on unrecoverable failure.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/cloud_agent/test_canva.py -v
```

- [ ] **Step 3: Implement minimal production path**

Sequence:

```text
SessionManager.ensure_service_ready("canva")
→ open configured template URL
→ upload six validated clips + voice.mp3
→ wait for upload completion
→ arrange 1→6 with straight cuts
→ mute source audio when narration is primary
→ place narration
→ generate captions
→ retain template caption styling
→ export MP4 1080p
→ download to output
```

Do not add transitions/effects in MVP.

- [ ] **Step 4: Verify GREEN and commit**

```bash
uv run pytest test/services/cloud_agent/test_canva.py -v
uv run ruff check app/services/cloud_agent/providers/canva.py test/services/cloud_agent/test_canva.py

git add app/services/cloud_agent/providers/canva.py test/services/cloud_agent/test_canva.py test/resources/cloud_agent/canva
git commit -m "feat: automate canva final assembly"
```

Live Canva verification is Task 13, not CI.

---

### Task 10: Full checkpointed production wiring + factory

**Files:**
- Modify: `app/services/cloud_agent/workflow.py`
- Modify: `app/services/cloud_agent/worker.py`
- Create: `app/services/cloud_agent/factory.py`
- Test: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**

```python
build_workflow() -> CloudAgentWorkflow
build_worker() -> CloudAgentWorker
python -m app.services.cloud_agent.worker
```

- [ ] **Step 1: Write failing full fake-E2E test**

Assert exact concrete status order:

```text
QUEUED
PREFLIGHT
PREFLIGHT_PASSED
TTS_GENERATING
TTS_READY
FLOW_GENERATING
FLOW_DOWNLOADING
FLOW_READY
CANVA_UPLOADING
CANVA_EDITING
CAPTIONING
EXPORTING
DOWNLOADING_FINAL
VALIDATING
FINAL_VALIDATED
COMPLETED
```

Assert checkpoint progression is only durable completed boundaries, e.g.:

```text
NONE → TTS_READY → FLOW_READY → FINAL_VALIDATED
```

- [ ] **Step 2: Add failure/recovery assertions**

Required:

- Preflight before TTS;
- Flow re-check immediately before Flow;
- Canva re-check immediately before Canva;
- lease remains renewed during external work;
- `HUMAN_REQUIRED` preserves checkpoint and stops;
- `PAUSE`/`CANCEL` stop at safe boundaries;
- resume from `FLOW_READY` validates voice + all six clips and skips TTS/Flow;
- invalid retained artifacts prevent unsafe skip;
- final validation occurs before cleanup;
- failed final validation retains clips;
- cleanup only after `FINAL_VALIDATED`.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -v
```

- [ ] **Step 4: Wire real components through `factory.py`**

Factory reads existing `config.app` and constructs Store/Storage/ProfileLock/Browser/Session/Preflight/TTS/Flow/Canva/Workflow/Worker. No global second config object.

- [ ] **Step 5: Implement bounded error classification**

Map:

```text
transient timeout/network → retry up to configured max
human security challenge  → HUMAN_REQUIRED
validation failure         → FAILED unless selective step retry applies
user pause                 → PAUSED
user cancel                → CANCELLED
```

- [ ] **Step 6: Verify GREEN and Gate C regression suite**

```bash
uv run pytest test/services/cloud_agent -v
uv run ruff check app/services/cloud_agent test/services/cloud_agent
```

- [ ] **Step 7: Commit**

```bash
git add app/services/cloud_agent/workflow.py app/services/cloud_agent/worker.py app/services/cloud_agent/factory.py test/services/cloud_agent/test_workflow.py
git commit -m "feat: wire cloud video production workflow"
```

---

# Gate D — VideosTurbo UI

### Task 11: Cloud Agent Streamlit control/status UI using FastAPI contract

**Files:**
- Create: `webui/cloud_agent.py`
- Modify: `webui/Main.py`
- Test: `test/services/test_cloud_agent_webui.py`
- Preserve/reuse:
  - `app/services/six_clip_plan.py`
  - `webui/six_clip_timeline.py` generation/session helpers where useful
  - existing script generation settings and tests

**Interfaces:**

`webui/cloud_agent.py` owns Cloud Agent API client calls and panels. It must not open SQLite directly.

- [ ] **Step 1: Write failing UI/API-client tests**

Using Streamlit `AppTest` and/or isolated helper tests, assert the Cloud Agent path exposes:

```text
Video Subject
Target Words
Language
Generate Script
Script Editor
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
Job History
Final Video
```

Assert Start payload contains `script`, existing `SixClipPlan`, `master_prompt`, language/target words and selected voice settings.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest test/services/test_cloud_agent_webui.py -v
```

- [ ] **Step 3: Implement thin API helpers**

Use configured loopback/reverse-proxy API base URL. Add bounded timeout/error messages. Do not duplicate job transition logic in Streamlit.

- [ ] **Step 4: Implement Service Connections + Cloud Agent health**

Display:

- Flow/Canva status + last checked;
- Check / Open Browser / Check All;
- Worker Online/Offline from `/cloud-agent/health` heartbeat;
- storage status when unhealthy.

- [ ] **Step 5: Implement Start/status/history/final UX**

Start creates a persistent job. Streamlit reruns/polls status but is not the worker. `HUMAN_REQUIRED` shows reason and Open Browser. Completed shows preview/download.

- [ ] **Step 6: Disconnect legacy media requirement from Cloud Agent Start**

Cloud Agent Start must not require six media Upload/URL slots. Keep legacy UI/code available during migration rather than deleting it now.

- [ ] **Step 7: Verify existing six-clip/script regressions**

```bash
uv run pytest test/services/test_cloud_agent_webui.py test/services/test_six_clip_webui.py test/services/test_webui_generation_defaults.py -v
uv run ruff check webui/cloud_agent.py webui/Main.py test/services/test_cloud_agent_webui.py
```

- [ ] **Step 8: Commit**

```bash
git add webui/cloud_agent.py webui/Main.py test/services/test_cloud_agent_webui.py
git commit -m "feat: add cloud production control ui"
```

---

# Gate E — Ubuntu services and protected human browser

### Task 12: API/WebUI/Worker systemd + Xvfb/noVNC/Nginx deployment

**Files:**
- Create: `deploy/systemd/videosturbo-api.service.example`
- Create: `deploy/systemd/videosturbo-worker.service.example`
- Modify: `deploy/systemd/videosturbo-webui.service.example`
- Create: `deploy/cloud-agent/README.md`
- Modify: `config.example.toml` only for deployment comments/defaults not already added in Task 4.

**Interfaces:**

- Existing WebUI service remains the Streamlit process.
- API service runs existing FastAPI/Uvicorn stack on loopback.
- Worker is independent from both.
- Xvfb/noVNC exposes the production headed browser only through protected access.

- [ ] **Step 1: Write deployment assertions/test script where practical**

Add a small parsing test if repository conventions allow, or use explicit verification commands in the runbook. Required service commands must contain:

```text
WebUI: python -m streamlit run /opt/VideosTurbo/webui/Main.py ...
API:   /opt/VideosTurbo/.venv/bin/python /opt/VideosTurbo/main.py
Worker:/opt/VideosTurbo/.venv/bin/python -m app.services.cloud_agent.worker
```

All run with `WorkingDirectory=/opt/VideosTurbo` and server-side environment files for secrets.

- [ ] **Step 2: Define API service**

`videosturbo-api.service.example` starts the existing `main.py`/`app.asgi:app` path, binds loopback in production config, restarts on failure and does not contain secrets inline.

- [ ] **Step 3: Define worker service**

Worker starts after network/API prerequisites as appropriate and restarts on failure. It does not depend on an open Streamlit session.

- [ ] **Step 4: Update/reuse WebUI service**

Preserve existing Streamlit command and existing deployment safeguards unless they conflict with the Cloud Agent architecture. Add only the API-base/reverse-proxy configuration needed for Cloud Agent UI calls.

- [ ] **Step 5: Write exact Ubuntu runbook**

Document:

```text
install system packages
→ install/sync project with uv
→ uv run playwright install --with-deps chromium
→ create server data/profile/lock directories with videosturbo ownership
→ configure SQLite/job storage permissions
→ configure Xvfb display
→ configure noVNC against that display
→ configure Nginx/TLS/auth or private-network access
→ install/enable API service
→ install/enable WebUI service
→ install/enable Worker service
→ first manual Google Flow/Canva login in headed server browser
→ Check All Sessions
```

- [ ] **Step 6: Document browser safety boundary**

State explicitly: noVNC is never anonymously exposed to the public internet; browser profile files are never served by FastAPI/Streamlit/Nginx static routes.

- [ ] **Step 7: Verify service syntax/runbook references and commit**

```bash
systemd-analyze verify deploy/systemd/videosturbo-api.service.example
systemd-analyze verify deploy/systemd/videosturbo-worker.service.example
systemd-analyze verify deploy/systemd/videosturbo-webui.service.example
```

If `systemd-analyze` rejects `.example` paths because referenced EnvironmentFiles/users do not exist in CI, record the exact limitation and validate unit syntax on the real Ubuntu host in Task 13.

```bash
git add deploy/systemd/videosturbo-api.service.example deploy/systemd/videosturbo-worker.service.example deploy/systemd/videosturbo-webui.service.example deploy/cloud-agent/README.md config.example.toml
git commit -m "docs: add ubuntu cloud agent deployment"
```

---

# Gate F — Automated verification + real Ubuntu End-to-End

### Task 13: Verification gates and real Ubuntu smoke

**Files:**
- Create: `docs/superpowers/plans/2026-08-22-cloud-video-agent-smoke-checklist.md`
- Modify production/tests only when a reproducible smoke bug is found; every fix follows RED → GREEN.

- [ ] **Step 1: Run complete automated verification**

```bash
uv sync --frozen
uv run python -m compileall app webui
uv run ruff check app webui test
uv run pytest
```

Expected: PASS and coverage >=70%.

- [ ] **Step 2: Verify API/WebUI/Worker service independence on Ubuntu**

```text
systemctl status videosturbo-api
systemctl status videosturbo-webui
systemctl status videosturbo-worker
```

Verify worker heartbeat becomes Online in UI/API without opening a local browser.

- [ ] **Step 3: Verify session controls**

Demonstrate:

1. Check All reports Flow + Canva READY.
2. Expire one service session while keeping Google account selectable; Auto Re-login restores READY.
3. Create a controlled human-verification state; system becomes `HUMAN_REQUIRED`.
4. `Open Browser` opens the protected noVNC session showing the server-side browser.
5. User resolves challenge manually; Check Again/Resume verifies READY.

Never bypass security challenges.

- [ ] **Step 4: Run one real paid-generation E2E job**

Evidence checklist:

```text
Script generated/edited using existing LLM
SixClipPlan + Master Prompt generated
job persisted/QUEUED
Preflight checks worker/storage/Flow/Canva
TTS generated using existing voice service
voice duration validated
Flow re-check
Flow generated six clips
six clips downloaded/validated
checkpoint=FLOW_READY
Canva re-check
six clips + narration uploaded
clips ordered / source audio muted as configured
captions generated
MP4 exported/downloaded
final.mp4 validated
checkpoint=FINAL_VALIDATED
Flow sources cleaned only after validation
status=COMPLETED
```

- [ ] **Step 5: Test local-browser/computer independence**

After Start, close the user's local browser. Confirm worker continues. Reopen from another client and confirm persisted progress/final result.

- [ ] **Step 6: Test worker restart + lease recovery at `FLOW_READY`**

Stop/restart worker after `FLOW_READY`. Verify:

```text
expired lease recovered
→ voice + all six clips revalidated
→ TTS call count does not increase
→ Flow generation call count does not increase
→ resume at Canva
```

- [ ] **Step 7: Test final-validation failure keeps sources**

Use a controlled invalid final artifact/export case. Assert source clips remain and job is not marked `COMPLETED`.

- [ ] **Step 8: Record evidence**

Smoke checklist records date/time, app commit SHA, job IDs, PASS/FAIL and sanitized notes. Never commit cookies, credentials, signed URLs or screenshots containing secrets.

- [ ] **Step 9: Commit smoke checklist**

```bash
git add docs/superpowers/plans/2026-08-22-cloud-video-agent-smoke-checklist.md
git commit -m "test: record cloud agent ubuntu smoke"
```

No legacy cleanup starts until Gate F passes.

---

# Gate G — Legacy cleanup after acceptance only

### Task 14: Legacy cleanup in a separate follow-up PR

**Files:**
- Determined by reference search after Task 13 acceptance.
- Do not perform this task inside the initial Cloud Agent implementation PR unless the user explicitly changes the gate.

- [ ] **Step 1: Build an actual reference/import/route/UI inventory**

Candidate categories:

```text
Pexels/Pixabay/Coverr stock providers
legacy Main stock search/download
material type + mixed image/video UI
old six-media Upload/URL controls in the main production path
Ken Burns/image processing used only by removed path
legacy local-render components with no retained callers
unused dependencies after source removal
```

Explicitly exclude retained Music Batch or other still-used features.

- [ ] **Step 2: For each category, write/update a regression test first**

Example cycle:

```text
RED/coverage guard for retained behavior
→ remove exactly one unused category
→ focused tests
→ full Ruff + pytest
→ commit
```

- [ ] **Step 3: Remove dependencies only after source references are zero**

Regenerate `uv.lock` and run `uv sync --frozen`.

- [ ] **Step 4: Final automated + real smoke**

```bash
uv sync --frozen
uv run python -m compileall app webui
uv run ruff check app webui test
uv run pytest
```

Then repeat one real Cloud Agent smoke before merging cleanup.

---

## Review Gates

```text
Gate A — Tasks 1–3
SQLite jobs/checkpoints/lease/heartbeat + storage + worker semantics

Gate B — Tasks 4–6
Playwright/process-safe profile lock + local/session Preflight + FastAPI contract

Gate C — Tasks 7–10
Existing TTS adapter + Flow + Canva + full workflow

Gate D — Task 11
Streamlit UI through API

Gate E — Task 12
Ubuntu API/WebUI/Worker + Xvfb/noVNC deployment

Gate F — Task 13
Full automated verification + real Ubuntu E2E/restart/session recovery

Gate G — Task 14
Separate legacy-cleanup follow-up only after acceptance
```

Every gate must leave the branch reviewable and testable.

## Spec Coverage Self-Review

- Existing FastAPI reuse: Tasks 6, 12.
- Existing state reviewed without unsafe reuse: Task 1 architecture/SQLite boundary.
- Existing LLM + six-clip reuse: Tasks 1, 11.
- Persist six prompt records: Task 1 via `SixClipPlan` JSON.
- Existing TTS reuse: Task 7.
- Existing storage/FFmpeg reuse: Task 2.
- Status/checkpoint separation: Tasks 1, 3, 10.
- Lease renewal + worker heartbeat: Tasks 1, 3, 10, 13.
- Local worker/storage Preflight: Tasks 5, 10, 13.
- Session Preflight before TTS: Tasks 5, 10, 13.
- Safe Auto Re-login/HUMAN_REQUIRED: Tasks 5, 8, 9, 10, 13.
- Manual Check/Open Browser: Tasks 6, 11, 12, 13.
- Process-safe browser profile ownership: Task 4.
- Headed Xvfb/noVNC recovery: Tasks 4, 12, 13.
- Re-check before Flow/Canva: Tasks 8, 9, 10.
- Six Flow clips/selective retry: Task 8.
- Canva timeline/narration/captions/export: Task 9.
- Final validation before cleanup: Tasks 2, 10, 13.
- Pause/Resume/Cancel without losing checkpoint: Tasks 3, 6, 10, 11.
- Restart-resume without repeating paid steps: Tasks 3, 10, 13.
- UI independent from worker: Task 11.
- Existing WebUI systemd reuse + separate API service: Task 12.
- Ubuntu 24/7 operation: Tasks 12–13.
- Legacy code retained until real E2E: Task 14 gate.
- Secrets/profile isolation: Tasks 4, 6, 12.

No task requires a live third-party session in CI. Live Google Flow and Canva behavior is verified only at Task 13.

## Placeholder / consistency self-review

- No `TBD`, `TODO`, or “implement later” steps remain.
- `status`, `checkpoint`, and `control_request` names are consistent across tasks.
- `SixClipPlan` is reused rather than duplicated.
- `renew_lease` and worker heartbeat are defined before worker/workflow tasks consume them.
- Flow/Canva browser locking is process-safe because API and worker are separate processes.
- Deployment includes all three runtime processes: API, WebUI, Worker.
- No generic `RUNNING` state is referenced.
- Cleanup remains after `FINAL_VALIDATED` only.
