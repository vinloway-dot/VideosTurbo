# VideosTurbo Cloud Video Agent Design

> Draft v2 for review — Session Preflight / Auto Re-login / Manual Session Check integrated.

## Goal

เปลี่ยน VideosTurbo จากระบบที่ Render วิดีโอเองเป็น **Video Production Orchestrator** ที่ให้ผู้ใช้สร้าง/แก้ Script แล้วกด `Start Auto Production` จากนั้นปิด Browser หรือปิดคอมได้ โดย Ubuntu Cloud Server ทำงานต่อจนได้ `final.mp4`.

Final target experience:

```text
Video Subject
→ Generate Script
→ Review/Edit Script
→ Start Auto Production
→ user may close browser/computer
→ Session Preflight
→ TTS API
→ Google Flow
→ Canva
→ Final Validation
→ Cleanup
→ COMPLETED
```

## Core Responsibilities

### VideosTurbo Web

- Video Subject
- Target Words
- Language
- Script generation/editing
- Six-clip analysis + Master Prompt generation
- TTS settings
- Start/Pause/Resume/Cancel production
- Service/session status
- Job progress/history
- Final video preview/download

### Cloud Agent

- Persist jobs and checkpoints
- Session Preflight
- Auto Re-login when safe
- TTS API orchestration
- Google Flow browser automation
- Canva browser automation
- Downloads/uploads
- Retry/Resume
- Human Required handoff
- Audio/video validation
- Cleanup

### External Services

- TTS: API only; no browser automation for TTS
- Google Flow: Browser Automation via Playwright
- Canva: Browser Automation via Playwright

## Deployment Target

Primary production environment:

- Ubuntu 24.04 LTS Cloud Server
- 4 vCPU
- 8 GB RAM
- 100 GB SSD/NVMe
- x86-64
- GPU not required for MVP

Server software:

- Python 3.11+
- FastAPI
- Streamlit/VideosTurbo WebUI
- Playwright
- Chromium/Chrome
- FFmpeg + ffprobe
- SQLite for initial Cloud Agent persistence
- Nginx
- systemd
- virtual display/noVNC for human login/debug

## UI Direction

Main Create Video screen should be simple and modern.

### Create Video

```text
Video Subject: [ Why Saturn Has a Hexagon ]
Target Words:  [ 130 ]
Language:      [ English ]

[ Generate Script ] [ View Master Prompt ]
```

### Script Editor

- editable script
- Regenerate
- Shorten
- Expand
- Copy

### Voice Settings

- TTS Provider
- Voice
- Speed

Primary action:

```text
[ Start Auto Production ]
```

### Service Connections

This section remains visible even though Start performs automatic checks.

```text
SERVICE CONNECTIONS

Google Flow
🟢 Ready
Last checked: 2 min ago
[ Check ] [ Open Browser ]

Canva
🟢 Ready
Last checked: 2 min ago
[ Check ] [ Open Browser ]

[ Check All Sessions ]
```

Supported session states:

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

### Production Status

Example:

```text
Script Ready             ✅
Voice Generated          ✅
Google Flow: 6/6 Clips   ✅
Canva Upload             ✅
Captions                 ⏳
Export MP4               Waiting
Final Validation         Waiting
```

Current Job also exposes:

- Job ID
- Duration target
- Output resolution
- current step/progress
- Pause / Resume / Cancel

Final result exposes:

- Preview
- Duration
- Resolution
- Subtitle status
- Voice
- Download MP4

## Workflow

### Step 1 — Generate Script

User supplies Subject, Target Words and Language. Existing LLM/script generation logic remains the content-generation source.

Persist:

```text
subject
script
target_words
language
```

State: `SCRIPT_READY`.

### Step 2 — Generate Master Prompt

Reuse six-clip logic to divide the narration into:

```text
Clip 1: 0–10 sec
Clip 2: 10–20 sec
Clip 3: 20–30 sec
Clip 4: 30–40 sec
Clip 5: 40–50 sec
Clip 6: 50–60 sec
```

Each clip keeps:

- Narration Context
- Video Prompt

Then combine the six prompts into one Master Prompt for Google Flow Agent Mode.

Persist `master_prompt` and six clip prompt records. State: `PROMPT_READY`.

### Step 3 — Start Auto Production + Session Preflight

Immediately after `Start Auto Production`, create/persist the job but **do not start TTS or spend generation credits yet**.

Initial state:

```text
PREFLIGHT
```

Agent checks:

1. Cloud Agent/worker health
2. Storage health/free space
3. Google Flow session
4. Canva session

Playwright must open the real service and verify authenticated functionality. Cookie existence alone is not sufficient.

#### Auto Re-login

If the Flow/Canva service session expired but the saved Google account session is still valid, Agent should try normal re-authentication automatically, such as:

```text
SESSION_EXPIRED
→ Continue with Google / choose saved Google account
→ return to service
→ verify authenticated page
→ READY
```

Successful Auto Re-login must continue automatically without asking the user.

#### Human Required

Do not bypass or automate security challenges that require a human, including:

- new password entry
- CAPTCHA
- 2FA
- Google Prompt
- Verify it's you
- other security challenge

Use:

```text
HUMAN_REQUIRED
```

The UI must show the affected service and an `Open Browser` action. The user resolves the challenge through the remote server browser/noVNC, then uses `Check Again` or `Resume`.

Resume must run Session Preflight again before continuing.

#### Preflight gate

Production may start only when required services are verified:

```text
Google Flow   READY
Canva         READY
Cloud Agent   READY
Storage       READY
```

Then transition:

```text
PREFLIGHT_PASSED
→ RUNNING
```

#### Re-check before each service

Passing initial Preflight does not guarantee a session stays valid. Re-check:

- Google Flow immediately before Flow work
- Canva immediately before Canva work

If a session expired, run the same Auto Re-login → Verify → Human Required fallback.

### Step 4 — TTS via API

TTS is a backend/API operation, not browser automation.

```text
script
→ configured TTS provider
→ audio bytes/file
→ storage/jobs/<job_id>/audio/voice.mp3
```

The architecture must allow existing/API providers to be reused behind one Cloud Agent interface.

States:

```text
TTS_GENERATING
TTS_READY
```

### Step 5 — Validate TTS Audio

Use ffprobe to verify:

- file exists
- non-zero/meaningful size
- readable audio stream
- readable codec
- duration

Target is approximately 60 seconds. Duration tolerance must be configurable; an initial operational target may be 58–62 seconds.

Do not continue blindly when duration falls outside the configured policy.

### Step 6 — Google Flow Automation

Before using Flow, re-check/recover session.

Then:

```text
Open Google Flow
→ Agent Mode
→ paste Master Prompt
→ Generate
→ monitor actual generation state
```

Do not rely on a fixed `sleep` as the only readiness signal.

### Step 7 — Download Flow Clips

Expected output: six clips.

```text
clip_01.mp4
clip_02.mp4
clip_03.mp4
clip_04.mp4
clip_05.mp4
clip_06.mp4
```

For every downloaded clip verify:

- file exists
- non-zero/meaningful size
- video stream readable
- duration readable

Retry only the failed clip/download when possible rather than recreating all six.

Persist checkpoint `FLOW_READY` only when all required clips are valid.

### Step 8 — Canva Automation

Before Canva work, re-check/recover session.

Prefer a prepared Canva video template for MVP to reduce browser automation surface. Template may predefine:

- 1080 × 1920 canvas
- caption style
- caption position
- font/style
- base audio/video layout

Agent workflow:

```text
Open Canva
→ upload six clips
→ upload voice.mp3
→ arrange clips 1→6
→ mute source video audio when narration should be primary
→ generate Auto Captions
→ apply template caption style
→ export MP4 1080p
→ download final.mp4
```

MVP should use simple straight cuts and avoid unnecessary timeline effects/transitions.

### Step 9 — Final Validation

A browser “download complete” event is not sufficient. Before cleanup, validate `final.mp4` with ffprobe/filesystem checks:

- file exists
- size above configured minimum
- readable video stream
- readable audio stream
- duration readable/within policy
- expected portrait resolution when configured (1080 × 1920)
- file is not corrupt

Only then set `FINAL_VALIDATED`.

### Step 10 — Cleanup

Never delete Flow source clips before Final Validation succeeds.

After `FINAL_VALIDATED`, temporary Flow/browser/upload/export files may be removed.

Retain by default:

- script
- Master Prompt
- voice audio
- final MP4
- job metadata

Then mark `COMPLETED`.

## Job States

Cloud Agent job states must include at least:

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
COMPLETED
PAUSED
HUMAN_REQUIRED
FAILED
CANCELLED
```

Service/session sub-status must support:

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

## Persistence, Queue and Resume

MVP uses one Worker and one active production at a time. Additional jobs remain queued.

Job state/checkpoints must be persistent so closing the web UI or restarting the server does not lose workflow position.

Examples:

```text
Job 1001 Running
Job 1002 Waiting
Job 1003 Waiting
```

Resume must continue from the latest safe checkpoint. Example: if TTS and Flow completed before Canva authentication failed, Resume starts from Canva after validating retained artifacts; it must not regenerate TTS or Flow unnecessarily.

Each external step should have bounded retry (default operational policy: up to 3 attempts) before moving to `FAILED` or `HUMAN_REQUIRED` based on error type.

## Storage Layout

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

## Job Data

Persist at least:

```text
id
subject
script
master_prompt
language
target_words
tts_provider
voice_id
voice_speed
status
current_step
progress
flow_status
canva_status
voice_file
final_video
error_code
error_message
created_at
started_at
completed_at
updated_at
```

## API Surface

Cloud Agent API should expose equivalent operations to:

```text
POST /jobs
GET /jobs/{job_id}
POST /jobs/{job_id}/pause
POST /jobs/{job_id}/resume
POST /jobs/{job_id}/cancel
GET /jobs/{job_id}/final

POST /sessions/check
POST /sessions/google-flow/check
POST /sessions/canva/check
POST /sessions/google-flow/repair
POST /sessions/canva/repair
POST /sessions/{service}/open-browser
```

Project routes should remain under the existing `/api/v1` convention.

## Browser Profiles

Use persistent server-side browser profiles and keep service concerns separated, e.g.:

```text
/data/browser/google-profile/
/data/browser/canva-profile/
```

Profiles must not be publicly exposed. Credentials and API secrets must never be committed to Git or sent to the client UI.

## Error Evidence

Browser automation errors must capture enough evidence for maintenance:

- screenshot
- service
- job id
- current step
- current URL when safe
- timestamp
- sanitized error message

Example:

```text
storage/jobs/<job_id>/screenshots/flow_generate_error.png
storage/jobs/<job_id>/screenshots/canva_upload_error.png
storage/jobs/<job_id>/logs/agent.log
```

## Production Services

Production should run independently from the user's browser through system services, e.g.:

```text
videosturbo-web.service
videosturbo-worker.service
```

After Ubuntu restart:

```text
web starts
→ worker starts
→ worker reads persisted jobs
→ verifies last checkpoint/artifacts
→ resumes safe incomplete job
```

## Migration Strategy

Do not remove the current working generation path before the new workflow passes real End-to-End tests.

Recommended migration:

1. Freeze working six-clip baseline.
2. Build Cloud Agent architecture on a separate branch.
3. Add persistent Job Manager/Worker.
4. Add Session Preflight + manual session controls.
5. Reuse API-based TTS through Cloud Agent.
6. Add Google Flow adapter.
7. Add Canva adapter.
8. Add Retry/Resume/Human Required.
9. Add simplified modern WebUI.
10. Deploy and smoke-test on Ubuntu Cloud Server.
11. Only after E2E success, remove legacy stock/render/UI code in small tested groups.

Potential later cleanup includes Pexels/Pixabay/Coverr stock paths, material-type UI, old six-media upload/URL controls, Ken Burns/image processing, and legacy local render paths that are no longer referenced.

Keep useful existing components such as LLM providers, script generation, six-clip prompt logic, configuration, logging, API foundation and reusable task/history concepts.

## MVP Acceptance Criteria

MVP is complete only when all are demonstrated:

1. Script can be generated, edited and saved.
2. User can manually check Google Flow/Canva sessions from the web UI.
3. `Start Auto Production` always runs Session Preflight first.
4. Ordinary expired service sessions can Auto Re-login using an already-authenticated Google account when possible.
5. CAPTCHA/2FA/verification pauses as `HUMAN_REQUIRED` and provides Open Browser/Resume flow.
6. TTS is generated via API and stored on the server.
7. TTS duration/file validity is checked.
8. Google Flow receives the Master Prompt and produces six clips.
9. Six clips are downloaded and validated.
10. Canva uploads/arranges the six clips and narration audio.
11. Canva generates captions.
12. Canva exports and the Agent downloads final MP4.
13. Server validates final MP4 before deleting sources.
14. Temporary source clips are cleaned only after validation.
15. Web UI shows completion/history and allows final video download.
16. The user's local computer/browser can be closed while production continues.
17. Retry/Resume works without repeating already successful paid generation steps unnecessarily.
18. Ubuntu restart does not permanently lose persistent job state.

## Future Scope (not MVP)

- multiple concurrent workers
- batch/scheduled production
- additional video generators (Kling/Sora/etc.)
- additional editors (CapCut/etc.)
- publishing to YouTube/Facebook/TikTok
- notification channels
- multi-user tenancy
- cloud object storage

## Baseline Principle

```text
VideosTurbo = Content + Control + Status
Cloud Agent = Workflow + Session Preflight + Auto Re-login + Retry + Resume
TTS = API
Google Flow = Browser Automation
Canva = Browser Automation
Ubuntu Cloud Server = 24/7 execution environment
```

The product goal is:

> **ใส่หัวข้อ → ตรวจ Script → กด Start → ปิดคอม → กลับมารับ Final Video**
