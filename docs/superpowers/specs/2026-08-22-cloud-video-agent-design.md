# VideosTurbo Cloud Video Agent Design

> **Status:** Design Spec v2.1 — repository-aligned planning baseline.  
> **Implementation gate:** No production coding starts until this spec, the implementation plan, and Draft PR #4 have been reviewed as a complete planning set.

## 1. Goal

เปลี่ยน VideosTurbo จากระบบที่ Render วิดีโอเองเป็น **Video Production Orchestrator (ระบบควบคุมการผลิตวิดีโอ)** ที่ให้ผู้ใช้:

```text
Video Subject
→ Generate Script
→ Review/Edit Script
→ Generate/View Master Prompt
→ Start Auto Production
→ user may close browser/computer
→ Cloud Agent continues on Ubuntu
→ final.mp4 validated
→ user returns to Preview / Download
```

Production flow:

```text
Start Auto Production
→ Session + Local Preflight
→ TTS API
→ Google Flow
→ Canva
→ Final Validation
→ Cleanup
→ COMPLETED
```

## 2. Baseline and migration boundary

Cloud Agent work is isolated on:

```text
feature/cloud-video-agent
```

The recoverable working baseline remains:

```text
feature/six-clip-media-timeline
7b17cbe9519d543b5b6eb42674de559e74d6280c
```

Do not merge or modify the baseline as part of this feature. Do not remove the legacy stock/local-render path until the Cloud Agent passes the real Ubuntu End-to-End smoke gate.

## 3. Repository-aligned reuse decisions

The design intentionally reuses existing implementation where it already provides the required behavior.

### 3.1 FastAPI foundation — reuse

Reuse:

- `app/asgi.py` FastAPI application.
- `app/router.py` root API router.
- `app/controllers/v1/base.py::new_router()` so Cloud Agent endpoints remain under `/api/v1`.
- Existing response/error conventions such as `utils.get_response(...)`, `HttpException`, `FileResponse`, and existing path-security helpers where applicable.

Cloud Agent must not create a second FastAPI application.

### 3.2 Existing task state — reviewed, not used as Cloud Agent durable store

`app/services/state.py` already provides `MemoryState` and optional `RedisState`. Existing task managers also provide an in-process/thread queue and an optional Redis queue for the legacy video flow.

These are useful reference patterns, but they are **not** the Cloud Agent durable job database because:

- `MemoryState` is process-local and is lost on restart.
- Redis is optional in the current application.
- The existing task manager is coupled to the legacy `tm.start`/`VideoParams` flow.
- Cloud Agent must resume durable checkpoints after worker/server restart even when Redis is disabled.

MVP therefore uses a separate SQLite-backed `CloudJobStore`, while preserving the old state/task paths unchanged for legacy features.

### 3.3 TTS — reuse existing API-backed voice routing

Reuse `app/services/voice.py::tts(...)`:

```python
tts(
    text: str,
    voice_name: str,
    voice_rate: float,
    voice_file: str,
    voice_volume: float = 1.0,
)
```

Cloud Agent adds only a thin adapter plus validation. It must not duplicate ElevenLabs/Azure/Gemini/MiniMax/MiMo/SiliconFlow/Chatterbox routing already owned by `voice.py`.

`tts_provider` remains persisted for UI/history, but server-side validation must ensure it matches the selected `voice_id` routing. The workflow must not maintain a second independent provider registry.

TTS remains API/backend-only. Do not automate a TTS website.

### 3.4 LLM and six-clip planning — reuse

Reuse:

- `app/services/llm.py::generate_script(...)` and existing provider/retry/config behavior.
- `app/services/six_clip_plan.py` for six fixed 10-second sections.
- `build_script_generation_requirements(...)`.
- `generate_six_clip_plan(...)`.
- `build_master_prompt(...)`.
- `app/models/six_clip.py::SixClipPlan` / `SixClipSegment`.

The Cloud Agent job persists the complete `SixClipPlan` as JSON in SQLite instead of defining another six-clip schema. This preserves all six `narration_context` and `video_prompt` records as well as the Master Prompt.

### 3.5 Storage and FFmpeg helpers — reuse

Reuse:

- `utils.storage_dir(sub_dir, create=True)` as the repository-aware default storage root.
- `utils.get_ffmpeg_binary()` as the FFmpeg resolution source.
- Existing path-security patterns before serving/download paths.

A Cloud Agent media-probe helper may resolve `ffprobe` beside the selected FFmpeg executable or from `PATH`, but must not duplicate general storage-root logic.

### 3.6 Configuration — reuse existing `[app]`

All non-secret Cloud Agent configuration belongs in existing `config.app` / `[app]` in `config.example.toml`.

Do not create a second config loader or a second TOML file.

## 4. Runtime architecture

MVP uses three independent server processes plus protected remote-desktop infrastructure:

```text
Nginx / TLS
   ├── VideosTurbo Streamlit WebUI
   │      └── loopback/public-proxied Cloud Agent API
   └── FastAPI API
             └── SQLite CloudJobStore
                    ▲
                    │
             Cloud Agent Worker
                    │
          Playwright + FFmpeg + files
```

Processes:

1. **WebUI service (บริการหน้าเว็บ)** — reuse/update `deploy/systemd/videosturbo-webui.service.example`.
2. **API service (บริการ FastAPI)** — add `videosturbo-api.service.example`, running the existing `app.asgi:app`/`main.py` stack on loopback behind Nginx.
3. **Worker service (บริการงานเบื้องหลัง)** — add `videosturbo-worker.service.example`.
4. **Xvfb/noVNC (จอเสมือน/รีโมตเบราว์เซอร์)** — protected by Nginx authentication, VPN, private network, or equivalent control; never anonymous public access.

Long-running production work never runs inside a Streamlit rerun or normal FastAPI request.

The Streamlit Cloud Agent UI uses the FastAPI control contract instead of independently mutating SQLite, so browser UI, future clients, and automation share one control surface.

## 5. Deployment target

Primary production environment:

- Ubuntu 24.04 LTS
- x86-64
- 4 vCPU
- 8 GB RAM
- 100 GB SSD/NVMe
- GPU not required for MVP
- Python >=3.11, matching the repository requirement

Primary software:

- FastAPI
- Streamlit
- SQLite
- Playwright
- Chromium/Chrome
- FFmpeg + ffprobe
- Nginx
- systemd
- Xvfb/noVNC

## 6. Persistent job model

### 6.1 Job status vs checkpoint

`status` is the current observable workflow state. `checkpoint` is the last durable completed boundary safe to resume from.

They are intentionally separate.

Examples:

```text
status=HUMAN_REQUIRED, checkpoint=FLOW_READY
status=PAUSED,         checkpoint=TTS_READY
status=CANVA_EDITING,  checkpoint=FLOW_READY
```

This prevents `PAUSED` or `HUMAN_REQUIRED` from destroying the information needed to resume safely.

### 6.2 Job states

Supported job states include:

```text
DRAFT
SCRIPT_READY
PROMPT_READY
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
PAUSED
HUMAN_REQUIRED
FAILED
CANCELLED
```

There is no generic `RUNNING` state. After `PREFLIGHT_PASSED`, the workflow moves to the next concrete state, normally `TTS_GENERATING`.

### 6.3 Control request

Persist a control request separate from status:

```text
NONE
PAUSE
CANCEL
```

Pause/cancel is checked at safe workflow boundaries. Resume clears the control request, verifies the durable checkpoint/artifacts, runs required Preflight/session checks, then requeues safely.

### 6.4 Required persisted data

Persist at least:

```text
id
subject
script
master_prompt
clip_plan_json
language
target_words

tts_provider
voice_id
voice_speed

status
checkpoint
control_request
current_step
progress

flow_status
canva_status

voice_file
final_video

error_code
error_message

worker_id
lease_until

created_at
started_at
completed_at
updated_at
```

Store `clip_plan_json` from existing `SixClipPlan`; do not create duplicate clip models.

## 7. Queue, lease, heartbeat and restart recovery

MVP executes one active production at a time. Other jobs remain `QUEUED`.

`CloudJobStore` owns atomic claiming with a worker lease. Long external operations must renew the lease so another worker cannot reclaim the same job while Google Flow or Canva is legitimately still running.

A worker heartbeat is persisted separately from individual job leases so the API/UI can report `Cloud Agent Online` without inspecting systemd directly.

Auto-claim rules:

- claim `QUEUED` work and explicitly resumable work whose lease is absent/expired;
- never auto-claim `DRAFT`, `SCRIPT_READY`, `PROMPT_READY`, `PAUSED`, `HUMAN_REQUIRED`, `COMPLETED`, `FAILED`, or `CANCELLED`;
- Resume is an explicit transition that returns a paused/human-required job to a resumable queued state only after required validation.

After server restart:

```text
API/WebUI start
→ Worker starts
→ Worker heartbeat updates
→ expired active lease becomes recoverable
→ validate checkpoint artifacts
→ resume at next safe step
```

## 8. File layout

```text
storage/
└── jobs/
    └── <job_id>/
        ├── input/
        │   ├── script.txt
        │   └── master_prompt.txt
        ├── audio/
        │   └── voice.mp3
        ├── flow/
        │   ├── clip_01.mp4
        │   ├── clip_02.mp4
        │   ├── clip_03.mp4
        │   ├── clip_04.mp4
        │   ├── clip_05.mp4
        │   └── clip_06.mp4
        ├── screenshots/
        ├── logs/
        └── final/
            └── final.mp4
```

`prepare(job_id)` creates directories and returns deterministic paths. It does **not** create empty placeholder media files. `write_inputs(...)` writes script/Master Prompt after validated input exists.

## 9. Local + Session Preflight

Immediately after the user presses `Start Auto Production`, persist a job and queue it. Before TTS or any paid generation work, the worker runs Preflight.

Required checks:

1. Worker is executing and has a valid worker identity/heartbeat.
2. Job storage root exists, is writable, and satisfies configured minimum free space.
3. Google Flow session is usable by opening the real service.
4. Canva session is usable by opening the real service.

Flow/Canva checks must verify authenticated functionality, not just Cookie existence.

### 9.1 Session states

```text
CHECKING
READY
SESSION_EXPIRED
AUTO_RELOGIN
LOGIN_REQUIRED
CAPTCHA_REQUIRED
2FA_REQUIRED
VERIFICATION_REQUIRED
ERROR
```

### 9.2 Auto Re-login

If a service session expired but an already-authenticated Google account is offered by the service:

```text
SESSION_EXPIRED
→ AUTO_RELOGIN
→ Continue with Google / choose saved account
→ verify service page
→ READY
```

Do not store a Google password in application config/database.

### 9.3 Human Required boundary

Never bypass:

- password challenge
- CAPTCHA
- 2FA
- Google Prompt
- Verify-it's-you
- equivalent security challenge

Use:

```text
HUMAN_REQUIRED
```

The worker preserves `checkpoint`, current evidence, and the browser state needed for manual recovery when safe.

### 9.4 Browser/noVNC model

Production Playwright runs in a virtual display environment compatible with noVNC. The production deployment uses a headed browser on Xvfb so `Open Browser` can display the same server-side browser session during `HUMAN_REQUIRED`.

`cloud_agent_browser_headless` remains configurable for tests/development, but the Ubuntu human-recovery deployment uses headed mode.

Persistent profiles are configurable server-side paths. Recommended production locations are outside the repository, for example:

```text
/var/lib/videosturbo/browser-profiles/google-flow
/var/lib/videosturbo/browser-profiles/canva
```

Only one process may use a service profile at a time. Browser profile access must use a process-safe lock/lease, not only a Python `threading.Lock`, because API and worker are separate processes.

The normal job worker owns profiles during production. Manual API session checks must acquire the same service lock; if the service is busy, they return a bounded busy response rather than opening a competing persistent context.

## 10. Workflow

### Step 1 — Script and six-clip plan

Reuse existing LLM and six-clip modules.

Persist:

```text
subject
script
language
target_words
SixClipPlan JSON
master_prompt
```

User may edit Script and view Master Prompt before Start.

### Step 2 — Start and Preflight

```text
QUEUED
→ PREFLIGHT
→ local health + Flow + Canva checks
→ PREFLIGHT_PASSED
→ TTS_GENERATING
```

No TTS/Flow credit should be consumed before Preflight passes.

### Step 3 — TTS

```text
script
→ existing voice.tts API-backed routing
→ voice.mp3
→ ffprobe validation
→ TTS_READY checkpoint
```

Validate:

- file exists
- meaningful non-zero size
- readable audio stream
- codec readable
- duration readable
- configured duration policy

Initial operational duration target may be 58–62 seconds, but limits are configuration, not hard-coded constants.

### Step 4 — Flow re-check and generation

Before Flow work:

```text
check Flow session
→ repair if safe
→ verify READY
→ generate
```

Then:

```text
Open configured Flow URL
→ Agent Mode
→ paste Master Prompt
→ Generate
→ monitor observable state
→ obtain six clips
```

Do not rely on a fixed `sleep` as the only readiness signal.

### Step 5 — Flow downloads and checkpoint

Expected durable files:

```text
clip_01.mp4 ... clip_06.mp4
```

Each clip must pass video validation. Retry only failed download/item where possible. Persist `FLOW_READY` only after all six artifacts validate.

### Step 6 — Canva re-check and final assembly

Before Canva:

```text
check Canva session
→ repair if safe
→ verify READY
```

MVP prefers a prepared Canva template.

Sequence:

```text
upload six validated clips
→ upload voice.mp3
→ order clips 1→6
→ straight cuts
→ mute source video audio when narration is primary
→ place narration
→ generate Auto Captions
→ use template caption style
→ export MP4 1080p
→ download final.mp4
```

Do not add unnecessary effects/transitions in MVP.

### Step 7 — Final Validation

A browser download-complete event is not enough.

Validate:

- file exists
- configured minimum size
- readable video stream
- readable audio stream
- readable duration
- expected resolution when configured (MVP target 1080x1920)
- ffprobe can read the file without corruption error

Only then persist:

```text
FINAL_VALIDATED
```

### Step 8 — Cleanup and completion

Only after `FINAL_VALIDATED`:

- delete temporary Flow source clips according to policy;
- delete upload/browser/export temporary files according to policy;
- retain Script, Master Prompt, `voice.mp3`, `final.mp4`, and job metadata by default.

Then:

```text
COMPLETED
```

If Final Validation fails, source clips remain available for retry/debug.

## 11. Retry and Resume

Each external step has bounded retry. Default operational policy is 3 attempts, configurable.

Classify failures:

- transient network/browser timeout → retry;
- deterministic invalid artifact → `FAILED` unless a step-specific retry can fix only that artifact;
- password/CAPTCHA/2FA/security challenge → `HUMAN_REQUIRED` immediately;
- user pause → preserve checkpoint and `PAUSED`;
- user cancel → `CANCELLED` at a safe boundary.

Resume must validate artifacts required by the checkpoint before skipping a paid step.

Example:

```text
checkpoint=FLOW_READY
voice.mp3 valid
six clips valid
→ re-check Canva
→ continue at Canva
```

Never regenerate TTS/Flow merely because Canva required human login.

## 12. API surface

Use existing `new_router()` so the effective prefix remains `/api/v1`.

Cloud Agent routes:

```text
GET  /api/v1/cloud-agent/health

POST /api/v1/cloud-agent/jobs
GET  /api/v1/cloud-agent/jobs
GET  /api/v1/cloud-agent/jobs/{job_id}
POST /api/v1/cloud-agent/jobs/{job_id}/pause
POST /api/v1/cloud-agent/jobs/{job_id}/resume
POST /api/v1/cloud-agent/jobs/{job_id}/cancel
GET  /api/v1/cloud-agent/jobs/{job_id}/final

POST /api/v1/cloud-agent/sessions/check
POST /api/v1/cloud-agent/sessions/google-flow/check
POST /api/v1/cloud-agent/sessions/canva/check
POST /api/v1/cloud-agent/sessions/google-flow/repair
POST /api/v1/cloud-agent/sessions/canva/repair
GET  /api/v1/cloud-agent/sessions/{service}/open-browser
```

`open-browser` is `GET` because it returns the configured protected noVNC URL; it does not bypass login or perform a security action itself.

Long-running production work is never executed inline in these handlers. Explicit manual session check/repair endpoints are bounded operations and must respect the shared browser-profile lock.

`GET /health` reports at least:

- Cloud Agent enabled/disabled
- worker last-seen timestamp / online status
- storage writable status
- free-space status

## 13. UI

### Create Video

- Video Subject
- Target Words
- Language
- Generate Script
- Script Editor
- Regenerate / Shorten / Expand / Copy
- View Master Prompt
- TTS Provider
- Voice
- Speed
- Start Auto Production

Reuse current script/six-clip logic. The Cloud Agent Start path must not require the legacy six-media Upload/URL fields.

### Service Connections

```text
Google Flow
Status / Last checked
[ Check ] [ Open Browser ]

Canva
Status / Last checked
[ Check ] [ Open Browser ]

[ Check All Sessions ]
```

### Cloud Agent status

Show worker Online/Offline using the persisted heartbeat/health endpoint.

### Production status

Show concrete workflow states and progress, with:

- Job ID
- current step
- checkpoint
- Pause / Resume / Cancel
- Human Required reason/evidence link when safe
- Job History

### Final result

- Preview
- Duration
- Resolution
- subtitle/caption status
- voice
- Download MP4

## 14. Configuration baseline

Cloud Agent settings live under `[app]`. Initial defaults/configuration include equivalents of:

```text
cloud_agent_enabled = false
cloud_agent_db_path = storage/cloud-agent.sqlite3
cloud_agent_worker_poll_seconds = 2
cloud_agent_worker_lease_seconds = configurable and longer than poll interval
cloud_agent_worker_heartbeat_seconds = configurable
cloud_agent_max_retries = 3
cloud_agent_min_free_disk_gb = configurable
cloud_agent_tts_min_duration_seconds = 58
cloud_agent_tts_max_duration_seconds = 62
cloud_agent_final_min_size_bytes = configurable
cloud_agent_expected_width = 1080
cloud_agent_expected_height = 1920
cloud_agent_browser_headless = configurable
cloud_agent_google_profile_dir = server-local path
cloud_agent_canva_profile_dir = server-local path
cloud_agent_remote_browser_url = protected noVNC URL
cloud_agent_flow_url = configured service URL
cloud_agent_canva_template_url = configured template URL
```

Never commit real credentials, cookies, browser profiles, signed download URLs, or API keys.

## 15. Error evidence

Browser failures record:

- service
- job id when applicable
- current step
- timestamp
- current URL when safe
- sanitized error message
- screenshot when it does not expose credentials/security secrets

Example:

```text
storage/jobs/<job_id>/screenshots/flow_generate_error.png
storage/jobs/<job_id>/logs/agent.log
```

## 16. Security boundaries

- No CAPTCHA/2FA/security-challenge bypass.
- No passwords in config/SQLite.
- API keys stay server-side.
- Browser profiles stay outside public static directories.
- Final-file endpoint must resolve the job-owned validated path; reject path traversal.
- noVNC must not be anonymous on the public internet.
- Nginx/TLS/authentication or equivalent access control is required for production exposure.

## 17. TDD and test strategy

All implementation follows **TDD (Test-Driven Development — เขียนการทดสอบก่อน)**:

```text
RED: write one failing behavior test
→ verify the failure is for the missing behavior
→ GREEN: minimal production code
→ verify focused tests
→ REFACTOR while green
→ run relevant regression tests
→ commit
```

CI/unit tests do not depend on a live Google Flow/Canva account. Provider adapters use deterministic local HTML fixtures/page-object tests for state detection. Real third-party behavior is verified only at the explicit Ubuntu smoke gate.

## 18. MVP acceptance criteria

MVP is complete only when all are demonstrated:

1. Script can be generated, edited and saved using existing LLM behavior.
2. Existing six-clip logic produces/persists six prompt records and Master Prompt.
3. User can manually check Flow/Canva sessions.
4. Start always performs local + session Preflight before TTS.
5. Ordinary expired session can Auto Re-login when an already-authenticated account is offered.
6. CAPTCHA/2FA/verification becomes `HUMAN_REQUIRED` without bypass.
7. noVNC shows the server-side browser for human recovery.
8. TTS uses existing API-backed voice routing and passes audio validation.
9. Flow generates six clips; all six are downloaded and validated.
10. Only failed Flow items/downloads are retried when possible.
11. Canva uploads/arranges six clips plus narration.
12. Canva generates captions and exports MP4.
13. `final.mp4` passes server-side validation before source cleanup.
14. User can Pause/Resume/Cancel at defined safe boundaries.
15. Resume validates checkpoint artifacts and does not repeat successful paid steps unnecessarily.
16. Worker lease/heartbeat prevents duplicate execution and exposes Cloud Agent online status.
17. Closing the local browser/computer does not stop production.
18. Worker/server restart preserves the job and resumes from a safe checkpoint.
19. WebUI shows history/status/final Preview/Download.
20. Legacy stock/render code remains available until this E2E gate passes.

## 19. Real Ubuntu smoke gate

Before declaring production-ready or starting legacy cleanup, demonstrate on Ubuntu 24.04:

```text
Generate/Edit Script
→ SixClipPlan + Master Prompt
→ Start
→ Preflight
→ TTS + validation
→ Flow session re-check
→ 6 Flow clips + validation
→ FLOW_READY checkpoint
→ Canva session re-check
→ upload/arrange/narration/captions
→ export/download
→ Final Validation
→ cleanup after validation only
→ COMPLETED
```

Also verify:

- Auto Re-login scenario.
- HUMAN_REQUIRED + noVNC + Resume scenario.
- local browser/computer closed during production.
- worker/server restart at a safe checkpoint.
- no duplicate TTS/Flow after resume from `FLOW_READY`.

## 20. Legacy cleanup gate

Legacy cleanup is a separate follow-up phase after the smoke gate passes. Remove one category at a time, with regression tests and commits between categories.

Candidates may include:

- Pexels/Pixabay/Coverr paths no longer used by retained features.
- material-type/mixed image-video controls.
- old six-media Upload/URL controls from the main production path.
- Ken Burns/image processing used only by the removed path.
- legacy local render code with no retained references.
- dependencies proven unused after source cleanup.

Do not remove Music Batch or other retained features merely because the new Create Video path no longer calls them.

## 21. Final baseline principle

```text
VideosTurbo = Content + Control + Status
FastAPI = shared control contract
Cloud Agent Worker = durable workflow execution
SQLite = Cloud Agent durable jobs/checkpoints/leases/heartbeat
TTS = existing API-backed voice service
Google Flow = Playwright browser automation
Canva = Playwright browser automation
Ubuntu Cloud Server = 24/7 execution environment
```

Product target:

> **ใส่หัวข้อ → ตรวจ Script → กด Start → ปิดคอม → กลับมารับ Final Video**
