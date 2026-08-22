# Cloud Video Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Before every production-code behavior change, use `superpowers:test-driven-development`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent Cloud Agent that lets a user generate/review a script in VideosTurbo, press `Start Auto Production`, close their local browser/computer, and later receive a validated final MP4 produced through one canonical TTS artifact, six Google Flow source clips, and Adaptive Six-Clip final assembly in Canva on Ubuntu Cloud Server.

**Architecture:** Reuse the current FastAPI, configuration, LLM/script generation, `SixClipPlan`, Master Prompt, TTS routing, storage-root and FFmpeg/ffprobe helpers. Keep six Google Flow clips for MVP. After Preflight, create the canonical `voice.mp3` once, preserve its exact decimal duration, calculate a safe Canva playback factor, reject before Flow only when the configured quality floor would be exceeded, then let Canva own clip ordering, playback adjustment, narration, captions and export. SQLite persists jobs/checkpoints/leases/heartbeat plus narration/playback timing so restart/resume never repeats successful paid TTS/Flow work merely to recover timing information.

**Tech Stack:** Python >=3.11, FastAPI, Streamlit, Pydantic, stdlib `sqlite3`, Playwright sync API, Chromium/Chrome, FFmpeg/ffprobe, existing `app.services.voice`, existing `app.services.llm`, pytest 9.1.1, Ruff 0.15.21, systemd, Xvfb/noVNC, Nginx.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md` — Design Spec v2.2, Adaptive Six-Clip + Canva Playback.

## Global Constraints

- Production target: Ubuntu 24.04 LTS, x86-64, initially 4 vCPU / 8 GB RAM / 100 GB SSD/NVMe.
- GPU is not required for MVP.
- Do not create a second FastAPI application; reuse `app/asgi.py`, `app/router.py`, and `app/controllers/v1/base.py::new_router()`.
- Do not replace or mutate legacy `app.services.state` / legacy task-manager behavior for Cloud Agent persistence; Cloud Agent uses its own SQLite store because restart-resume is mandatory even when Redis is disabled.
- Reuse `app.services.llm.generate_script`, `app.services.six_clip_plan`, `app.models.six_clip.SixClipPlan`, and `app.services.voice.tts` rather than duplicating provider logic.
- TTS must use backend/API integration; do not automate TTS provider web pages.
- The Cloud Agent creates/reuses one canonical production `voice.mp3`; it must not synthesize a disposable full-length preview and then synthesize production audio again.
- Preserve exact decimal narration duration from ffprobe. Do not `ceil()` before Adaptive timing calculations.
- MVP keeps exactly six Google Flow source clips. Variable clip counts are deferred to a separate long-video design.
- Canva owns Cloud Agent final assembly. Do not route Cloud Agent final assembly through legacy `six_clip_render.py` or pre-concatenate the six production clips with FFmpeg.
- Adaptive timing uses base visual duration `60.0` seconds and configurable `cloud_agent_canva_min_playback_speed = 0.85`.
- For narration `D <= 60`, use `canva_playback_speed = 1.0` and target final duration `60.0`.
- For narration `D > 60`, use `canva_playback_speed = 60.0 / D` and target final duration `D`.
- If required playback speed is below the configured floor, fail with `NARRATION_TOO_LONG_FOR_SIX_CLIP` before consuming Google Flow generation credit.
- `Start Auto Production` must run local + session Preflight before TTS or paid generation work.
- Local Preflight verifies worker heartbeat, storage writability, and configured minimum free disk.
- Session Preflight opens the real Flow/Canva services; cookie existence alone is not sufficient.
- Safe Auto Re-login may select an already-authenticated Google account; password/CAPTCHA/2FA/Google Prompt/verification challenges become `HUMAN_REQUIRED` and are never bypassed.
- Keep manual `Check`, `Check All Sessions`, `Open Browser`, `Check Again`, and `Resume` UI paths.
- Re-check Flow immediately before Flow work and Canva immediately before Canva work.
- Persist `status` separately from the last durable `checkpoint`.
- Persist the existing `SixClipPlan` as JSON; do not create a duplicate six-clip domain model.
- Persist `audio_duration_seconds`, `canva_playback_speed`, and `target_final_duration_seconds` server-side.
- MVP runs one active worker/job at a time; additional jobs remain queued.
- Long external steps renew their worker lease; expired leases are recoverable only after checkpoint/artifact validation.
- Browser persistent profiles are shared across API/worker processes and require a process-safe lock, not only `threading.Lock`.
- Production human recovery uses headed Playwright on Xvfb/noVNC; headless remains a configurable development/test option.
- Canva automation must prefer role/text/accessible-label/input selectors plus observable post-action verification; coordinate-only clicking is not the primary strategy.
- Do not delete Flow source clips before Final Validation succeeds.
- Final Validation must verify the exported duration against `target_final_duration_seconds` and ensure narration is not truncated outside configured tolerance.
- Do not remove legacy stock/render paths until the new workflow passes real End-to-End Ubuntu smoke testing.
- API keys, credentials, browser profiles, cookies and signed media URLs must never be committed to Git or returned to the client.
- Keep project dependency convention: exact pins in `pyproject.toml`, `uv.lock`, pytest, Ruff and the current >=70% coverage floor.

---

## Current Implementation Baseline — Do Not Recreate Completed Work

Verified on `feature/cloud-video-agent` after Design Spec v2.2 commit `b41df45f697a45c3d1b75ba13497a95767527010`:

- [x] Task 1 baseline — Cloud Agent models + SQLite jobs/checkpoints/leases/worker heartbeat.
- [x] Task 2 baseline — per-job storage + ffprobe media probing/validation.
- [x] Task 3 baseline — persistent worker + pause/cancel/checkpoint resume + lease renewal.
- [x] Task 4 config v2.1 defaults were added through the existing config loader.
- [x] `playwright==1.62.0` was added to `pyproject.toml`.
- [ ] `uv.lock` still needs to be regenerated from a real `uv lock` result; do not invent package hashes.
- [ ] Temporary CI dependency-lock diagnostic in `.github/workflows/ci.yml` must be replaced with a permanent non-mutating `uv lock --check` after the generated lock diff is applied.
- [ ] Task 4 browser profile locking / browser manager implementation has not started.

Previous verified CI before the v2.2 correction passed Windows smoke, Python 3.11 and Python 3.13. Reuse the existing working code and tests; the next work is the v2.2 delta, not a rewrite.

---

## Repository Facts the Plan Must Preserve

- `app/asgi.py` already creates the FastAPI app and includes `root_api_router`.
- `app/controllers/v1/base.py::new_router()` applies `/api/v1`.
- `app/services/state.py` provides `MemoryState` / optional `RedisState`, but default MemoryState is not restart-durable.
- `app/services/voice.py::tts(text, voice_name, voice_rate, voice_file, voice_volume=1.0)` already routes API-backed TTS providers.
- `app/services/llm.py::generate_script(...)` centralizes LLM provider behavior.
- `app/services/six_clip_plan.py` and `app/models/six_clip.py` own the six planning sections and Master Prompt construction.
- `app/services/cloud_agent/media_probe.py::probe_media()` already returns a float duration from ffprobe JSON.
- `app/services/cloud_agent/workflow.py` currently validates TTS against a configured min/max and requires exactly six Flow files; the six-file requirement remains, the fixed narration max does not.
- `app/services/cloud_agent/storage.py` already owns deterministic `clip_01.mp4` … `clip_06.mp4` paths.
- `app/utils/utils.py::storage_dir(...)` and `get_ffmpeg_binary()` already exist.
- `deploy/systemd/videosturbo-webui.service.example` already runs Streamlit; update/reuse it rather than creating a duplicate WebUI service.
- `main.py` starts the existing FastAPI app with Uvicorn; production deployment needs separate API and worker services in addition to WebUI.

---

# Gate A2 — v2.2 Adaptive Timing Correction to Completed Tasks 1–3

### Task 3A: Durable Adaptive timing fields + compatible SQLite/config evolution

**Files:**
- Modify: `app/models/cloud_agent.py`
- Modify: `app/services/cloud_agent/job_store.py`
- Modify: `app/config/config.py`
- Modify: `config.example.toml`
- Modify: `test/services/cloud_agent/test_models.py`
- Modify: `test/services/cloud_agent/test_job_store.py`
- Modify: `test/services/test_config.py`

**Interfaces produced:**

```python
class CloudJobRecord(CloudJobCreate):
    audio_duration_seconds: float = Field(default=0.0, ge=0)
    canva_playback_speed: float = Field(default=1.0, gt=0, le=1.0)
    target_final_duration_seconds: float = Field(default=60.0, gt=0)
```

SQLite columns:

```text
audio_duration_seconds REAL NOT NULL DEFAULT 0
canva_playback_speed REAL NOT NULL DEFAULT 1
target_final_duration_seconds REAL NOT NULL DEFAULT 60
```

v2.2 config defaults:

```text
cloud_agent_tts_min_duration_seconds = 1
cloud_agent_canva_min_playback_speed = 0.85
cloud_agent_final_duration_tolerance_seconds = 1.0
```

`cloud_agent_tts_max_duration_seconds` is obsolete for Cloud Agent policy and must not remain in `CLOUD_AGENT_DEFAULTS` or `config.example.toml`. An old user `config.toml` may still contain that key; Cloud Agent code simply does not consume it as a narration ceiling.

- [ ] **Step 1: RED — model/default tests**

```python
def test_cloud_job_record_has_restart_safe_timing_defaults(job_request):
    record = make_record(job_request)
    assert record.audio_duration_seconds == 0.0
    assert record.canva_playback_speed == 1.0
    assert record.target_final_duration_seconds == 60.0


def test_cloud_agent_v22_defaults_remove_fixed_tts_ceiling():
    assert config.CLOUD_AGENT_DEFAULTS["cloud_agent_tts_min_duration_seconds"] == 1
    assert config.CLOUD_AGENT_DEFAULTS["cloud_agent_canva_min_playback_speed"] == 0.85
    assert config.CLOUD_AGENT_DEFAULTS["cloud_agent_final_duration_tolerance_seconds"] == 1.0
    assert "cloud_agent_tts_max_duration_seconds" not in config.CLOUD_AGENT_DEFAULTS
```

Also assert the example config contains the three v2.2 values and no fixed TTS max ceiling.

- [ ] **Step 2: Run RED**

```bash
uv run pytest test/services/cloud_agent/test_models.py test/services/test_config.py -k "cloud_agent or timing" -v
```

Expected: FAIL because timing fields/defaults are not yet v2.2.

- [ ] **Step 3: GREEN — add record fields and v2.2 config defaults**

Do not add timing fields to `CloudJobCreate`; they are server-derived, not client-authoritative inputs.

- [ ] **Step 4: RED — SQLite migration/round-trip tests**

Create a database with the pre-v2.2 `cloud_agent_jobs` schema, insert one legacy row, instantiate the current `CloudJobStore`, then assert:

```python
migrated = store.get_job("legacy-job")
assert migrated.audio_duration_seconds == 0.0
assert migrated.canva_playback_speed == 1.0
assert migrated.target_final_duration_seconds == 60.0

updated = store.patch_job(
    migrated.id,
    audio_duration_seconds=63.25,
    canva_playback_speed=60.0 / 63.25,
    target_final_duration_seconds=63.25,
)
assert updated.audio_duration_seconds == 63.25
```

- [ ] **Step 5: Run migration RED**

```bash
uv run pytest test/services/cloud_agent/test_job_store.py -k "timing or migration" -v
```

Expected: FAIL because old databases lack the new columns and timing fields are not patchable.

- [ ] **Step 6: GREEN — evolve schema safely**

During `_initialize()`, inspect `PRAGMA table_info(cloud_agent_jobs)` and `ALTER TABLE ... ADD COLUMN` only for missing v2.2 columns. Add the three fields to row mapping, create values and `_MUTABLE_COLUMNS`. Do not drop/recreate the jobs table.

- [ ] **Step 7: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/test_config.py -v
uv run ruff check app/models/cloud_agent.py app/services/cloud_agent/job_store.py app/config/config.py test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/test_config.py
```

Expected: PASS.

```bash
git add app/models/cloud_agent.py app/services/cloud_agent/job_store.py app/config/config.py config.example.toml test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/test_config.py
git commit -m "feat: persist adaptive cloud timing"
```

---

### Task 3B: Adaptive timing policy + audio validation without fixed max

**Files:**
- Create: `app/services/cloud_agent/timing.py`
- Modify: `app/services/cloud_agent/errors.py`
- Modify: `app/services/cloud_agent/media_probe.py`
- Create: `test/services/cloud_agent/test_timing.py`
- Modify: `test/services/cloud_agent/test_media_probe.py`

**Interfaces produced:**

```python
@dataclass(frozen=True)
class AdaptiveTiming:
    audio_duration_seconds: float
    canva_playback_speed: float
    target_final_duration_seconds: float


def calculate_adaptive_timing(
    audio_duration_seconds: float,
    *,
    base_visual_duration_seconds: float = 60.0,
    min_playback_speed: float = 0.85,
) -> AdaptiveTiming: ...


class NarrationTooLongError(MediaValidationError):
    error_code = "NARRATION_TOO_LONG_FOR_SIX_CLIP"
```

Change audio validation to:

```python
validate_audio(
    path: Path,
    *,
    min_duration: float,
    max_duration: float | None = None,
) -> MediaProbe
```

- [ ] **Step 1: RED — exact timing-policy tests**

```python
@pytest.mark.parametrize(
    ("duration", "speed", "target"),
    [
        (55.0, 1.0, 60.0),
        (60.0, 1.0, 60.0),
        (63.0, 60.0 / 63.0, 63.0),
        (70.0, 60.0 / 70.0, 70.0),
    ],
)
def test_calculate_adaptive_timing(duration, speed, target):
    result = calculate_adaptive_timing(duration, min_playback_speed=0.85)
    assert result.audio_duration_seconds == duration
    assert result.canva_playback_speed == pytest.approx(speed)
    assert result.target_final_duration_seconds == target


def test_decimal_duration_is_not_ceiled_before_calculation():
    result = calculate_adaptive_timing(62.1, min_playback_speed=0.85)
    assert result.audio_duration_seconds == 62.1
    assert result.canva_playback_speed == pytest.approx(60.0 / 62.1)


def test_required_speed_below_floor_is_rejected():
    with pytest.raises(NarrationTooLongError) as exc:
        calculate_adaptive_timing(71.0, min_playback_speed=0.85)
    assert exc.value.error_code == "NARRATION_TOO_LONG_FOR_SIX_CLIP"
```

Also reject non-finite/zero/negative duration, base duration <= 0, and floor outside `(0, 1]`.

- [ ] **Step 2: Run timing RED**

```bash
uv run pytest test/services/cloud_agent/test_timing.py -v
```

Expected: FAIL because `timing.py` does not exist.

- [ ] **Step 3: GREEN — implement pure timing policy**

No browser, DB or TTS calls are allowed in `timing.py`; keep it deterministic and independently testable.

- [ ] **Step 4: RED — prove audio validator accepts >62 when no max is supplied**

```python
def test_validate_audio_allows_longer_valid_audio_without_max(...):
    probe = validate_audio(audio_path, min_duration=1.0)
    assert probe.duration == pytest.approx(63.25)
```

Retain an explicit `max_duration` test so the generic validator can still enforce a max for callers that request one.

- [ ] **Step 5: GREEN — make `max_duration` optional**

Keep `_validate_duration()` generic. Do not remove its max behavior; only stop requiring a max for Cloud Agent narration.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_timing.py test/services/cloud_agent/test_media_probe.py -v
uv run ruff check app/services/cloud_agent/timing.py app/services/cloud_agent/errors.py app/services/cloud_agent/media_probe.py test/services/cloud_agent/test_timing.py test/services/cloud_agent/test_media_probe.py
```

```bash
git add app/services/cloud_agent/timing.py app/services/cloud_agent/errors.py app/services/cloud_agent/media_probe.py test/services/cloud_agent/test_timing.py test/services/cloud_agent/test_media_probe.py
git commit -m "feat: add adaptive six clip timing policy"
```

---

### Task 3C: Workflow timing gate, restart-safe revalidation + final-duration gate

**Files:**
- Modify: `app/services/cloud_agent/workflow.py`
- Modify: `test/services/cloud_agent/test_workflow.py`

**Constructor delta:**

```python
CloudAgentWorkflow(
    ...,
    tts_min_duration: float,
    canva_min_playback_speed: float,
    final_duration_tolerance_seconds: float,
    final_min_size_bytes: int,
    expected_width: int,
    expected_height: int,
)
```

Remove `tts_max_duration` from Cloud Agent workflow construction.

- [ ] **Step 1: RED — 63-second TTS continues and persists timing before Flow**

Use fake media probes and assert:

```text
TTS generates canonical voice.mp3 once
→ probe duration = 63.25
→ persist audio_duration_seconds = 63.25
→ persist canva_playback_speed = 60 / 63.25
→ persist target_final_duration_seconds = 63.25
→ checkpoint = TTS_READY
→ Flow is allowed next
```

- [ ] **Step 2: RED — narration beyond policy fails before Flow**

For duration `71.0` and floor `0.85`, assert:

```python
assert result.status is CloudJobStatus.FAILED
assert result.error_code == "NARRATION_TOO_LONG_FOR_SIX_CLIP"
assert flow.calls == 0
assert canva.calls == 0
```

- [ ] **Step 3: RED — resume at `TTS_READY` reuses audio and revalidates timing**

Assert TTS call count stays zero on resume, existing `voice.mp3` is re-probed, persisted timing is validated/reconciled from the exact duration, and Flow proceeds only if policy still passes.

- [ ] **Step 4: RED — resume at `FLOW_READY` never repeats paid work**

Assert valid audio + six Flow clips cause:

```text
TTS calls = 0
Flow calls = 0
Canva calls = 1
```

and Canva receives a job with the persisted playback/target-duration values.

- [ ] **Step 5: RED — final duration validation**

For target `63.25` and tolerance `1.0`, assert a final duration near target passes; a final duration that truncates narration by more than tolerance fails and Flow sources remain.

- [ ] **Step 6: Run workflow RED**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -k "timing or narration or duration or resume" -v
```

- [ ] **Step 7: GREEN — implement timing integration**

At TTS completion:

```text
validate_audio(min only)
→ use returned/probed decimal duration
→ calculate_adaptive_timing(...)
→ patch three durable timing fields
→ TTS_READY
```

At checkpoint validation, re-probe the retained audio and verify/recompute the timing values without synthesizing new audio. Catch `NarrationTooLongError` as a deterministic `FAILED` with its error code before Flow.

At Final Validation, use the returned `MediaProbe.duration` and configured tolerance. Cleanup remains strictly after `FINAL_VALIDATED`.

- [ ] **Step 8: Verify Gate A2 and commit**

```bash
uv run pytest test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/cloud_agent/test_timing.py test/services/cloud_agent/test_media_probe.py test/services/cloud_agent/test_worker.py test/services/cloud_agent/test_workflow.py -v
uv run ruff check app/models/cloud_agent.py app/services/cloud_agent test/services/cloud_agent
```

```bash
git add app/services/cloud_agent/workflow.py test/services/cloud_agent/test_workflow.py
git commit -m "feat: apply adaptive timing to cloud workflow"
```

**Gate A2 review:** Confirm a 63-second fake workflow reaches Flow, a >policy narration stops before Flow, and `TTS_READY` / `FLOW_READY` resumes do not repeat paid steps.

---

# Gate B — Browser runtime, Preflight and FastAPI contract

### Task 4: Finish Playwright dependency lock + process-safe browser profiles

**Files:**
- Existing modified: `pyproject.toml` (`playwright==1.62.0` already present)
- Modify: `uv.lock`
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`
- Create: `app/services/cloud_agent/browser_lock.py`
- Create: `app/services/cloud_agent/browser.py`
- Create: `test/services/cloud_agent/test_browser_lock.py`
- Create: `test/services/cloud_agent/test_browser.py`

**Interfaces:**

```python
BrowserService = Literal["google_flow", "canva"]
ProfileLock.acquire(service: str, *, timeout_seconds: float) -> ContextManager[None]
PersistentBrowserManager.open(service: str, *, headed: bool | None = None)
PersistentBrowserManager.capture_evidence(...)
```

- [ ] **Step 1: Resolve the already-pinned Playwright lock without invented hashes**

Use the existing GitHub Actions diagnostic that runs `uv lock` and prints `git diff -- uv.lock` as the resolver source of truth. Apply that exact generated diff to `uv.lock`. Do not hand-construct Playwright/greenlet/pyee wheel hashes.

- [ ] **Step 2: Replace temporary failing diagnostic with permanent lock gate**

The workflow must use a non-mutating check:

```yaml
- name: Check dependency lock
  run: uv lock --check
```

Keep `uv sync --frozen` after the lock check.

- [ ] **Step 3: Verify dependency gate**

```bash
uv lock --check
uv sync --frozen
```

Expected in CI: lock check and sync succeed on supported Python jobs; Playwright browser binaries are not committed and are not installed automatically by application startup.

- [ ] **Step 4: RED — cross-process profile lock tests**

Use `multiprocessing` and a temporary lock directory. Process A holds `google_flow`; Process B times out. A `canva` lock remains independently acquirable.

- [ ] **Step 5: GREEN — OS-level file lock**

Implement process-safe advisory locking for Ubuntu and supported Windows test behavior behind `browser_lock.py`. Do not use only `threading.Lock`.

- [ ] **Step 6: RED — browser manager tests**

Patch Playwright and assert dedicated profile paths, `launch_persistent_context(user_data_dir=...)`, configured headless/headed override, same-service exclusion, and job-owned screenshot + HTML evidence capture.

- [ ] **Step 7: GREEN — persistent browser manager**

Use `playwright.sync_api.sync_playwright()`. Never use the operator's personal/default Chrome profile.

- [ ] **Step 8: Runtime ignore rules**

Existing broad `/storage/` behavior may already cover runtime state. Add explicit comments/rules only where they improve clarity without hiding test fixtures. Browser profiles, locks and SQLite DB/WAL/SHM must never be committed.

- [ ] **Step 9: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_browser_lock.py test/services/cloud_agent/test_browser.py test/services/test_config.py -v
uv run ruff check app/services/cloud_agent/browser_lock.py app/services/cloud_agent/browser.py test/services/cloud_agent/test_browser_lock.py test/services/cloud_agent/test_browser.py
```

```bash
git add uv.lock .github/workflows/ci.yml .gitignore app/services/cloud_agent/browser_lock.py app/services/cloud_agent/browser.py test/services/cloud_agent/test_browser_lock.py test/services/cloud_agent/test_browser.py
git commit -m "feat: add cloud agent browser runtime"
```

On the target host install Chromium once with `uv run playwright install chromium` or deployment's `--with-deps` command; runtime application code must never install it automatically.

---

### Task 5: Session policy + local/session Preflight

**Files:**
- Create: `app/services/cloud_agent/preflight.py`
- Create: `app/services/cloud_agent/session.py`
- Create: `app/services/cloud_agent/providers/__init__.py`
- Create/extend: `app/services/cloud_agent/providers/google_flow.py`
- Create/extend: `app/services/cloud_agent/providers/canva.py`
- Test: `test/services/cloud_agent/test_preflight.py`
- Test: `test/services/cloud_agent/test_session.py`
- Test: `test/services/cloud_agent/test_google_flow.py`
- Test: `test/services/cloud_agent/test_canva.py`

**Provider session contract:**

```python
check_session(*, headed: bool = False) -> SessionCheckResult
repair_session(*, headed: bool = False) -> SessionCheckResult
```

**Policy:**

```python
SessionManager.check_all() -> dict[str, SessionCheckResult]
SessionManager.ensure_service_ready(service: str, job_id: str) -> SessionCheckResult
SessionManager.ensure_all_ready(job_id: str) -> dict[str, SessionCheckResult]
PreflightManager.ensure_ready(job_id: str, worker_id: str) -> PreflightResult
```

- [ ] **Step 1: RED — session classification**

Local fixture/page tests cover authenticated → READY, login → SESSION_EXPIRED, safe Continue-with-Google → READY, password/login challenge, CAPTCHA, 2FA, verification and network ERROR.

- [ ] **Step 2: GREEN — SessionManager policy**

Manager owns Check → bounded safe repair → Verify. Providers own only service-specific detection/actions. Never store Google passwords.

- [ ] **Step 3: RED — local Preflight**

Required failures: storage not writable, disk below configured minimum, worker identity/heartbeat invalid, Flow not ready after bounded safe repair, Canva challenge before TTS.

- [ ] **Step 4: GREEN — Preflight order**

```text
verify executing worker identity/heartbeat
→ verify storage writable + free-space threshold
→ SessionManager.ensure_all_ready(job_id)
```

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_preflight.py test/services/cloud_agent/test_session.py test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_canva.py -v
uv run ruff check app/services/cloud_agent/preflight.py app/services/cloud_agent/session.py app/services/cloud_agent/providers test/services/cloud_agent
```

```bash
git add app/services/cloud_agent/preflight.py app/services/cloud_agent/session.py app/services/cloud_agent/providers test/services/cloud_agent/test_preflight.py test/services/cloud_agent/test_session.py test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_canva.py
git commit -m "feat: add cloud agent preflight"
```

---

### Task 6: FastAPI Cloud Agent control/session API

**Files:**
- Create: `app/controllers/v1/cloud_agent.py`
- Modify: `app/router.py`
- Test: `test/services/test_cloud_agent_controller.py`

**Effective routes under `/api/v1`:**

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

- [ ] **Step 1: RED — API tests**

Assert job creation is persistent but does not execute TTS/Playwright inline; pause/resume/cancel transitions are validated; session calls honor browser-profile locks; final endpoint serves only job-owned `FINAL_VALIDATED` files.

Job detail must expose server-derived timing fields after TTS:

```python
assert data["audio_duration_seconds"] == pytest.approx(63.25)
assert data["canva_playback_speed"] == pytest.approx(60.0 / 63.25)
assert data["target_final_duration_seconds"] == pytest.approx(63.25)
```

Client job creation must not be allowed to authoritatively set those fields.

- [ ] **Step 2: GREEN — controller + router registration**

Use `new_router()`, repository response/error conventions and safe `FileResponse`. Keep long production work outside request handlers.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest test/services/test_cloud_agent_controller.py test/services/test_controller_base.py -v
uv run ruff check app/controllers/v1/cloud_agent.py app/router.py test/services/test_cloud_agent_controller.py
```

```bash
git add app/controllers/v1/cloud_agent.py app/router.py test/services/test_cloud_agent_controller.py
git commit -m "feat: expose cloud agent api"
```

---

# Gate C — TTS, Flow, Canva Playback and full workflow

### Task 7: Existing TTS adapter — canonical production audio once

**Files:**
- Create: `app/services/cloud_agent/tts.py`
- Test: `test/services/cloud_agent/test_tts.py`
- Reuse unchanged: `app/services/voice.py`

**Interface:**

```python
class ExistingVoiceTTSClient:
    def generate(self, job: CloudJobRecord, output_path: Path) -> Path: ...
```

Calls existing routing:

```python
voice.tts(
    text=job.script,
    voice_name=job.voice_id,
    voice_rate=job.voice_speed,
    voice_file=str(output_path),
)
```

- [ ] **Step 1: RED — adapter tests**

Assert exact arguments, provider/voice consistency validation, `voice.tts` failure handling, missing/empty artifact handling, and valid 63-second audio is not rejected by an old 60/62-second ceiling.

- [ ] **Step 2: GREEN — thin adapter only**

Do not duplicate provider HTTP clients. The adapter generates the canonical file; workflow/media-probe/timing modules own exact duration policy.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_tts.py test/services/test_voice.py -v
uv run ruff check app/services/cloud_agent/tts.py test/services/cloud_agent/test_tts.py
```

```bash
git add app/services/cloud_agent/tts.py test/services/cloud_agent/test_tts.py
git commit -m "feat: add cloud agent tts adapter"
```

---

### Task 8: Google Flow generation + selective download retry

**Files:**
- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Test: `test/services/cloud_agent/test_google_flow.py`
- Fixtures: `test/resources/cloud_agent/google_flow/{ready,login,challenge,generating,results}.html`

**Interface:**

```python
generate_and_download(
    job: CloudJobRecord,
    flow_dir: Path,
    expected_count: int = 6,
) -> list[Path]
```

- [ ] **Step 1: RED — deterministic page-object tests**

Cover Agent Mode, Master Prompt insertion, observable 2/6 → 4/6 → 6/6 progress, stable result order, bounded timeout, deterministic clip names, selective retry and validation failure.

- [ ] **Step 2: GREEN — production Flow adapter**

```text
SessionManager.ensure_service_ready("google_flow")
→ open configured Flow URL
→ Agent Mode
→ submit job.master_prompt
→ observe completion rather than fixed sleep alone
→ collect exactly six items
→ download/validate clip_01 … clip_06
```

Never log/persist signed download URLs.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -v
uv run ruff check app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py
```

```bash
git add app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py test/resources/cloud_agent/google_flow
git commit -m "feat: automate google flow generation"
```

---

### Task 9: Real Canva Playback Automation Spike — hard gate before production adapter

**Files:**
- Create after the live spike: `docs/superpowers/spikes/2026-08-22-canva-playback-spike.md`
- No production Canva assembly code is written in this task.

**Spike success contract:**

1. Playwright selects one uploaded video clip reliably without coordinate-only automation.
2. It opens Canva Playback through observable selectors.
3. It applies a custom speed around `0.95x`.
4. It observes evidence that playback/duration changed.
5. The same action works for all six clips.
6. Final visual end can be trimmed/bounded to the narration-derived target within configured tolerance.
7. The workflow works in headed Xvfb/noVNC production-style Chromium.

- [ ] **Step 1: Prepare one disposable Canva test design**

Use non-sensitive test media: six short numbered clips and one narration/test audio artifact. Do not use production credentials in logs or committed files.

- [ ] **Step 2: Run headed Playwright using the same persistent-profile manager planned for production**

Exercise one clip manually-assisted if necessary to discover stable role/text/input selectors; record only selector strategy and sanitized observations.

- [ ] **Step 3: Prove `0.95x` can be applied and verified**

Capture sanitized evidence showing selected clip, Playback control and an observable duration/timeline change. Do not rely solely on “click succeeded”.

- [ ] **Step 4: Repeat across six clips and bound final duration**

Verify identical playback factor can be applied to each clip and final overshoot can be corrected without changing only the last clip's motion speed.

- [ ] **Step 5: Record PASS/FAIL**

The spike document records date, Canva editor behavior, browser mode, selectors/observable states, six-clip repetition result, trim result and final recommendation. Never record cookies, tokens or private signed URLs.

- [ ] **Step 6: Gate decision**

If all seven success-contract items pass, continue to Task 10. If any required item cannot be made reliable, stop implementation and revise the Design Spec before deeper Canva automation. Do not silently move final concatenation back into VideosTurbo.

```bash
git add docs/superpowers/spikes/2026-08-22-canva-playback-spike.md
git commit -m "docs: record canva playback automation spike"
```

---

### Task 10: Canva Adaptive Six-Clip assembly, narration, captions + export

**Files:**
- Modify: `app/services/cloud_agent/providers/canva.py`
- Test: `test/services/cloud_agent/test_canva.py`
- Fixtures: `test/resources/cloud_agent/canva/{ready,login,challenge,editor,playback,export}.html`

**Interface remains:**

```python
assemble_and_export(
    job: CloudJobRecord,
    clips: list[Path],
    audio: Path,
    output: Path,
) -> Path
```

The adapter reads server-derived `job.canva_playback_speed` and `job.target_final_duration_seconds`.

- [ ] **Step 1: RED — page-object tests from successful spike behavior**

Cover upload completion, ordering 1→6, `1.0x` no-adjust path, `<1.0x` uniform adjustment across all six clips, post-action verification, narration at time 0, source mute, final trim/bound, captions, MP4 1080p export and final download.

- [ ] **Step 2: RED — unsafe UI behavior becomes explicit failure**

If Playback control cannot be found or the resulting timeline cannot be verified, raise a typed error that workflow can map to `HUMAN_REQUIRED` with `checkpoint=FLOW_READY` when manual recovery is appropriate.

- [ ] **Step 3: GREEN — implement only spike-proven selectors/actions**

```text
SessionManager.ensure_service_ready("canva")
→ open configured template
→ upload six validated clips + canonical voice.mp3
→ arrange 1→6
→ if speed < 1.0, apply same custom speed to every clip
→ verify timeline/playback result
→ mute source audio
→ place narration at 0
→ trim final visual end for rounding/overshoot if required
→ Auto Captions
→ export MP4 1080p
→ download
```

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_canva.py -v
uv run ruff check app/services/cloud_agent/providers/canva.py test/services/cloud_agent/test_canva.py
```

```bash
git add app/services/cloud_agent/providers/canva.py test/services/cloud_agent/test_canva.py test/resources/cloud_agent/canva
git commit -m "feat: automate adaptive canva assembly"
```

---

### Task 11: Full checkpointed production wiring + factory

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

- [ ] **Step 1: RED — full fake E2E for 63-second narration**

Assert:

```text
QUEUED → PREFLIGHT → PREFLIGHT_PASSED
→ TTS_GENERATING → TTS_READY (63.25s, ~0.9486x)
→ FLOW_GENERATING → FLOW_DOWNLOADING → FLOW_READY
→ CANVA_UPLOADING → CANVA_EDITING → CAPTIONING
→ EXPORTING → DOWNLOADING_FINAL → VALIDATING
→ FINAL_VALIDATED → COMPLETED
```

Checkpoint progression remains durable boundaries only.

- [ ] **Step 2: Recovery/failure assertions**

Cover Preflight before TTS, Flow/Canva re-checks, lease renewal, HUMAN_REQUIRED preserving checkpoint, pause/cancel, resume from TTS_READY/FLOW_READY without duplicate paid calls, `NARRATION_TOO_LONG_FOR_SIX_CLIP` before Flow, final-duration failure retaining sources, cleanup only after FINAL_VALIDATED.

- [ ] **Step 3: GREEN — factory reads existing `config.app`**

Construct Store/Storage/ProfileLock/Browser/Session/Preflight/TTS/Flow/Canva/Workflow/Worker from the existing config object. No second config loader.

- [ ] **Step 4: Verify Gate C and commit**

```bash
uv run pytest test/services/cloud_agent -v
uv run ruff check app/services/cloud_agent test/services/cloud_agent
```

```bash
git add app/services/cloud_agent/workflow.py app/services/cloud_agent/worker.py app/services/cloud_agent/factory.py test/services/cloud_agent/test_workflow.py
git commit -m "feat: wire adaptive cloud video workflow"
```

---

# Gate D — VideosTurbo UI

### Task 12: Cloud Agent Streamlit control/status UI through FastAPI

**Files:**
- Create: `webui/cloud_agent.py`
- Modify: `webui/Main.py`
- Test: `test/services/test_cloud_agent_webui.py`

- [ ] **Step 1: RED — UI/API-client tests**

Required controls/status: Video Subject, Target Words, Language, Generate Script, Script Editor, View Master Prompt, TTS Provider, Voice, Speed, Flow/Canva checks, Open Browser, Start, Pause/Resume/Cancel, status/history/final video.

After TTS_READY, status displays measured narration duration and Canva playback factor when below 1.0x. For `NARRATION_TOO_LONG_FOR_SIX_CLIP`, show measured duration, required speed, configured floor and actions: shorten script, reduce Target Words, or increase Voice Rate.

- [ ] **Step 2: GREEN — thin API helpers and panels**

Streamlit never opens SQLite directly and never becomes the worker. Cloud Agent Start does not require legacy six-media Upload/URL slots.

- [ ] **Step 3: Regression verification and commit**

```bash
uv run pytest test/services/test_cloud_agent_webui.py test/services/test_six_clip_webui.py test/services/test_webui_generation_defaults.py -v
uv run ruff check webui/cloud_agent.py webui/Main.py test/services/test_cloud_agent_webui.py
```

```bash
git add webui/cloud_agent.py webui/Main.py test/services/test_cloud_agent_webui.py
git commit -m "feat: add cloud production control ui"
```

---

# Gate E — Ubuntu services and protected human browser

### Task 13: API/WebUI/Worker systemd + Xvfb/noVNC/Nginx deployment

**Files:**
- Create: `deploy/systemd/videosturbo-api.service.example`
- Create: `deploy/systemd/videosturbo-worker.service.example`
- Modify: `deploy/systemd/videosturbo-webui.service.example`
- Create: `deploy/cloud-agent/README.md`

- [ ] **Step 1: Define independent services**

Required commands:

```text
WebUI: python -m streamlit run /opt/VideosTurbo/webui/Main.py ...
API:   /opt/VideosTurbo/.venv/bin/python /opt/VideosTurbo/main.py
Worker:/opt/VideosTurbo/.venv/bin/python -m app.services.cloud_agent.worker
```

All use `WorkingDirectory=/opt/VideosTurbo`; secrets come from server-side environment/config files, not unit files.

- [ ] **Step 2: Exact Ubuntu runbook**

Document system packages, `uv sync --frozen`, `uv run playwright install --with-deps chromium`, server data/profile/lock ownership, SQLite permissions, Xvfb display, noVNC, protected Nginx/TLS/auth/private access, service installation, first headed Flow/Canva login and session check.

- [ ] **Step 3: Safety boundary**

noVNC is never anonymous on the public internet; browser profiles are never served by application/static routes.

- [ ] **Step 4: Verify and commit**

```bash
systemd-analyze verify deploy/systemd/videosturbo-api.service.example
systemd-analyze verify deploy/systemd/videosturbo-worker.service.example
systemd-analyze verify deploy/systemd/videosturbo-webui.service.example
```

If host-specific users/EnvironmentFiles prevent local verification, validate the exact units on the real Ubuntu host in Gate F and record the limitation.

```bash
git add deploy/systemd/videosturbo-api.service.example deploy/systemd/videosturbo-worker.service.example deploy/systemd/videosturbo-webui.service.example deploy/cloud-agent/README.md
git commit -m "docs: add ubuntu cloud agent deployment"
```

---

# Gate F — Automated verification + real Ubuntu End-to-End

### Task 14: Verification gates and real Ubuntu smoke

**Files:**
- Create: `docs/superpowers/plans/2026-08-22-cloud-video-agent-smoke-checklist.md`
- Modify production/tests only for reproducible smoke bugs; every behavior fix follows RED → GREEN.

- [ ] **Step 1: Complete automated verification**

```bash
uv lock --check
uv sync --frozen
uv run python -m compileall app webui
uv run ruff check app webui test
uv run pytest
```

Expected: PASS and coverage >=70%.

- [ ] **Step 2: Runtime independence**

Verify API/WebUI/Worker service status and worker heartbeat without an open local browser.

- [ ] **Step 3: Session recovery**

Demonstrate READY, safe Auto Re-login, controlled HUMAN_REQUIRED, protected noVNC manual resolution and Resume. Never bypass security challenges.

- [ ] **Step 4: Real ~63-second paid E2E**

Evidence must show one canonical TTS, decimal duration around 63 seconds, calculated playback around 0.95x, six Flow clips, Canva uniform playback adjustment, same voice.mp3, captions/export, final duration within tolerance, FINAL_VALIDATED before cleanup, COMPLETED.

- [ ] **Step 5: Over-policy narration stops before Flow**

Use narration requiring playback below `0.85x`. Verify `NARRATION_TOO_LONG_FOR_SIX_CLIP`, Flow generation is not started and the UI gives actionable guidance.

- [ ] **Step 6: Local-browser independence**

Start a job, close the user's local browser/computer, confirm worker continues, then reconnect and observe persisted state/final output.

- [ ] **Step 7: Restart recovery at TTS_READY and FLOW_READY**

At TTS_READY: restart worker, re-probe existing audio, do not recreate TTS. At FLOW_READY: restart worker, validate existing voice + six clips, do not recreate TTS/Flow, resume at Canva.

- [ ] **Step 8: Final-validation failure retains sources**

Controlled invalid/short final export must not become COMPLETED and source Flow clips remain available.

- [ ] **Step 9: Record sanitized evidence and commit**

Checklist records date/time, commit SHA, job IDs, measured narration duration, playback factor, target/final durations, PASS/FAIL and sanitized notes. Never commit cookies, credentials, signed URLs or secret-bearing screenshots.

```bash
git add docs/superpowers/plans/2026-08-22-cloud-video-agent-smoke-checklist.md
git commit -m "test: record adaptive cloud agent ubuntu smoke"
```

No legacy cleanup starts until Gate F passes.

---

# Gate G — Legacy cleanup after acceptance only

### Task 15: Legacy cleanup in a separate follow-up PR

Do not perform this task inside the initial Cloud Agent PR unless the user explicitly changes the gate.

- [ ] **Step 1: Build actual references/imports/routes/UI inventory**

Review stock providers, legacy Main stock path, material type/mixed UI, old six-media Upload/URL controls, Ken Burns/image processing, legacy local-render components and dependencies. Explicitly exclude Music Batch and any retained callers.

- [ ] **Step 2: Remove one proven-unused category per TDD/regression cycle**

```text
retained-behavior test/coverage guard
→ remove one unused category
→ focused tests
→ full Ruff + pytest
→ commit
```

- [ ] **Step 3: Remove dependencies only after references are zero**

Regenerate `uv.lock`, run `uv lock --check` and `uv sync --frozen`.

- [ ] **Step 4: Repeat real Cloud Agent smoke before cleanup merge**

The cleanup PR must not change Adaptive Six-Clip timing or Canva assembly behavior.

---

## Review Gates

```text
Gate A baseline — Tasks 1–3 already complete and previously CI-green

Gate A2 — Tasks 3A–3C
v2.2 durable timing + timing policy + workflow/resume/final-duration correction

Gate B — Tasks 4–6
Playwright lock/profile runtime + local/session Preflight + FastAPI contract

Gate C — Tasks 7–11
Existing TTS adapter + Flow + real Canva Playback Spike + Canva adapter + full wiring

Gate D — Task 12
Streamlit UI through API

Gate E — Task 13
Ubuntu API/WebUI/Worker + Xvfb/noVNC deployment

Gate F — Task 14
Full automated verification + ~63s E2E + over-policy/restart/session recovery smoke

Gate G — Task 15
Separate legacy-cleanup follow-up after acceptance
```

Every gate must leave the branch reviewable and testable. Draft PR #4 remains Draft until Gate F passes.

## Spec Coverage Self-Review

- Existing FastAPI reuse: Tasks 6, 13.
- Existing state reviewed without unsafe reuse: completed Task 1.
- Existing LLM + SixClipPlan reuse: completed Task 1, Tasks 8, 12.
- One canonical production TTS: Tasks 3C, 7, 11, 14.
- Decimal duration without early ceil: Tasks 3B–3C, 14.
- Durable timing fields and SQLite evolution: Task 3A.
- Adaptive formula / 0.85 floor: Tasks 3B–3C.
- Reject over-policy narration before Flow credit: Tasks 3C, 11, 14.
- Exactly six Flow clips in MVP: Tasks 8, 11, 14.
- Canva owns final assembly; no legacy pre-concat: Tasks 9–11, 14.
- Real Canva Playback hard gate: Task 9.
- Uniform six-clip playback + post-action verification: Tasks 9–10.
- Final duration/narration truncation gate: Tasks 3C, 10–11, 14.
- Restart/resume without duplicate paid steps: completed Task 3 plus Tasks 3C, 11, 14.
- Process-safe browser profile ownership: Task 4.
- Local/session Preflight: Task 5.
- Safe Auto Re-login/HUMAN_REQUIRED: Tasks 5, 10–11, 14.
- Manual Check/Open Browser: Tasks 6, 12–14.
- Headed Xvfb/noVNC recovery: Tasks 9, 13–14.
- UI timing/error explanation: Tasks 6, 12.
- Legacy code retained until real E2E: Task 15 gate.

## Placeholder / Consistency Self-Review

- No `TBD`, `TODO`, “implement later”, or undefined variable-N clip tasks are part of MVP.
- `status`, `checkpoint`, `control_request`, `audio_duration_seconds`, `canva_playback_speed`, and `target_final_duration_seconds` use consistent names throughout.
- `SixClipPlan` remains the MVP planning model; Dynamic Clip Timeline is not partially introduced.
- `cloud_agent_tts_max_duration_seconds` is explicitly obsolete for Cloud Agent v2.2 and is not used as a fixed narration gate.
- Browser dependency lock work uses a generated `uv lock` result, never fabricated package hashes.
- Canva Playback Spike occurs before production Canva assembly code.
- Cleanup remains after `FINAL_VALIDATED` only.
- Deployment still has three runtime processes: API, WebUI, Worker.
- No generic `RUNNING` status is introduced.
