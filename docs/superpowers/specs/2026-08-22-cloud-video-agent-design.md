# VideosTurbo Cloud Video Agent Design

> **Status:** Design Spec v2.4 — Adaptive Six-Clip + durable paid Google Flow generation fence.
> **Implementation gate:** Production Google Flow work remains paused until the v2.4 RED→GREEN implementation, live-job remediation, full verification, and CI are complete. This document authorizes no paid Flow/TTS operation and does not authorize restarting the production Worker.

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

MVP ยังคงใช้ **SixClipPlan จำนวน 6 คลิป** แต่เปลี่ยนนโยบายเวลาเป็น **Adaptive Six-Clip (หกคลิปแบบปรับความยาวใน Canva)** เพื่อรองรับเสียงบรรยายที่ยาวเกิน 60 วินาทีเล็กน้อยโดยไม่สร้าง TTS ซ้ำและไม่เพิ่มจำนวนคลิปใน Google Flow

Production flow:

```text
Start Auto Production
→ Session + Local Preflight
→ create real TTS once
→ probe exact narration duration
→ calculate required Canva playback speed
→ reject before Flow if speed would exceed the configured safety policy
→ recover valid local Flow artifacts or exclusively pre-clean the dedicated Flow project
→ durably fence the paid Flow side effect before Generate can be clicked
→ Google Flow generates, semantically renames, and bulk-downloads six clips when recovery is unavailable
→ atomically clear the paid-generation fence and persist FLOW_READY before remote Flow cleanup
→ Canva uploads/arranges six clips
→ Canva adjusts clip playback speed when required
→ Canva adds narration + captions
→ Canva exports final.mp4
→ Final Validation against the narration-derived target duration
→ Cleanup
→ COMPLETED
```

**Cloud Agent does not concatenate the six production clips inside VideosTurbo.** Final assembly is owned by Canva. FFmpeg/ffprobe remain local utilities for media probing, validation, and other retained legacy paths, not the Cloud Agent's primary final-assembly engine.

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

The current feature implementation completed Tasks 1–14 through the live paid-flow checkpoint. Those components are retained where compatible. This v2.4 spec keeps the v2.3 dedicated shared Flow workspace and v2.2 Adaptive Six-Clip timing policy, but adds a conservative durable fence before the paid Generate click so a crash at `TTS_READY` cannot cause automatic pre-clean and duplicate generation.

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

### 3.4 No extra Full Voice Preview in the Cloud Agent start path

Cloud Agent does **not** synthesize a disposable full-length preview and then synthesize production audio again.

After Preflight passes, it creates the canonical production artifact exactly once:

```text
script + selected voice/rate
→ existing voice.tts(...)
→ storage/jobs/<job_id>/audio/voice.mp3
→ ffprobe exact duration
→ reuse the same voice.mp3 through Canva and final output
```

A previously generated reusable voice artifact may be reused only when its script/voice/rate identity is proven current by the existing cache/fingerprint rules. A stale preview must never control a new job.

### 3.5 LLM and six-clip planning — reuse

MVP continues to reuse:

- `app/services/llm.py::generate_script(...)` and existing provider/retry/config behavior.
- `app/services/six_clip_plan.py` for six visual sections.
- `build_script_generation_requirements(...)`.
- `generate_six_clip_plan(...)`.
- `build_master_prompt(...)`.
- `app/models/six_clip.py::SixClipPlan` / `SixClipSegment`.

The Cloud Agent job persists the complete `SixClipPlan` as JSON in SQLite instead of defining another clip schema. Google Flow still produces exactly six source clips in MVP.

The fixed `0–10`, `10–20`, ... `50–60` segment ranges remain the **planning/reference timeline**, not a hard assertion that final narration must end at exactly 60 seconds. When narration exceeds 60 seconds within policy, Canva stretches the visual playback uniformly while preserving six chronological visual sections.

Dynamic clip counts are explicitly deferred to a later phase for materially longer videos.

### 3.6 Storage and FFmpeg helpers — reuse, but not Cloud Agent final assembly

Reuse:

- `utils.storage_dir(sub_dir, create=True)` as the repository-aware default storage root.
- `utils.get_ffmpeg_binary()` as the FFmpeg resolution source.
- Existing path-security patterns before serving/download paths.
- Cloud Agent `ffprobe` media validation.

For the Cloud Agent production path:

- FFprobe measures exact audio/video duration and validates streams/codecs/resolution.
- Google Flow creates the six source clips.
- Canva owns ordering, playback-speed adjustment, narration placement, captions, and final export.
- Do **not** pre-concatenate the six Cloud Agent clips with the legacy `six_clip_render.py` path before Canva.

### 3.7 Configuration — reuse existing `[app]`

All non-secret Cloud Agent configuration belongs in existing `config.app` / `[app]` in `config.example.toml`.

Do not create a second config loader or a second TOML file.

### 3.8 Google Flow project — one dedicated shared automation workspace

The configured Google Flow project is a dedicated VideosTurbo automation workspace. Do not create one project per CloudJob. The project contains no permanent/manual user assets; every product clip in it is a temporary automation artifact.

The existing cross-process `google_flow` persistent-profile lock is also the exclusive Flow workspace lock. A production Flow run holds the same lock and browser context across:

```text
inspect/pre-clean
→ settled actionable-editor verification
→ observable empty verification
→ durable paid-generation fence
→ paid generation
→ Agent rename
→ semantic-name verification
→ bulk ZIP download
→ local validation/materialization
→ durable FLOW_READY
→ remote post-job cleanup
→ observable empty verification
```

Session-check/control endpoints retain bounded short lock timeouts and may report busy. A production Flow run may wait longer for the shared workspace, renewing its worker lease while it waits. No process may inspect, clean, generate, rename, or download from the project while another process owns the workspace lock.

No global Flow workspace-state table is required. Each fresh generation owner pre-cleans after acquiring the lock, independent of previous job history. A job with `flow_generation_unresolved=true` is not a fresh generation owner: it enters reconciliation and must never pre-clean, delete remote assets, or click Generate.

The provider/workflow boundary is context-managed:

```python
with flow.acquire_workspace(job) as workspace:
    if recovered_clips:
        clips = recovered_clips
    elif job.flow_generation_unresolved:
        clips = workspace.reconcile_and_download(job, flow_dir)
    else:
        workspace.prepare_for_generation()
        job = workflow.persist_generation_fence(job)
        clips = workspace.generate_and_download(job, flow_dir)
    job = workflow.persist_flow_ready(
        clips,
        generation_unresolved=False,
        cleanup_unresolved=True,
    )
    workspace.cleanup_and_verify_empty()
    job = workflow.confirm_flow_cleanup(job)
```

`GoogleFlowClient`/`FlowWorkspaceRun` own browser actions, observable remote state, the ZIP download boundary, and the long-lived lock. `CloudAgentWorkflow` owns local recovery priority, checkpoint/control transitions, and cleanup-error suppression. `CloudJobStore` owns the compatible SQLite migration and atomic single-job update. The provider never writes SQLite.

### 3.9 v2.4 implementation delta from the current branch

The current branch already has the v2.3 workspace lock, semantic bulk ZIP, local salvage, `flow_cleanup_unresolved`, and post-`FLOW_READY` cleanup behavior. The paid smoke exposed these remaining gaps:

- SQLite and `CloudJobRecord` have no `flow_generation_unresolved` fence between `TTS_READY` and the paid remote side effect.
- `FlowWorkspaceRun.generate_and_download()` performs pre-clean and clicks Generate inside one provider call, so the workflow cannot durably fence the click while keeping SQLite out of the provider.
- A restarted `TTS_READY` job always re-enters the fresh path and may delete partial paid output before generating again.
- Empty verification can accept a transient loading state because zero checkboxes plus initial empty text does not prove the virtualized media inventory is settled.
- Editor readiness incorrectly shares the 1,800-second generation timeout.
- `FlowWorkspaceVerificationError` can escape the workflow, terminate the Worker process, and let systemd reclaim the still-claimable job.

The v2.4 implementation changes only these boundaries and preserves existing TTS, Canva, archive validation, local salvage, legacy render/stock, API, deployment, profile ownership, and public-network behavior.

### 3.10 One-time live-job remediation

The paid smoke job `c604f5d5-c206-4d49-bad2-cac59e2815a2` has one canonical `voice.mp3` with measured duration `63.936` seconds and playback speed `0.9384384384384384`, plus unresolved remote Flow assets. After the compatible schema migration is installed and while the production Worker remains stopped, one durable update sets:

```text
status=HUMAN_REQUIRED
checkpoint=TTS_READY
flow_generation_unresolved=true
error_code=FLOW_GENERATION_RECONCILIATION_REQUIRED
```

The remediation preserves the canonical audio, measured timing, remote assets, browser profile, and locks. It performs no pre-clean, delete, Generate, TTS, rename, download, or cleanup action. The Worker remains stopped after remediation; a later restart or paid retry requires a separate explicit gate.

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
          Playwright + ffprobe + files
                    │
       Google Flow → Canva → final.mp4
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
voice_file
audio_duration_seconds
canva_playback_speed
target_final_duration_seconds

status
checkpoint
control_request
current_step
progress

flow_status
canva_status
flow_generation_unresolved
flow_cleanup_unresolved

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

`audio_duration_seconds` must preserve the measured decimal duration from ffprobe. Do not `ceil()` it before timing-policy calculations.

`canva_playback_speed` is the approved playback factor for all six Flow clips for that job. Persisting it makes restart/resume deterministic.

`target_final_duration_seconds` is:

```text
max(60.0, audio_duration_seconds)
```

for the Adaptive Six-Clip MVP.

`flow_generation_unresolved` is a conservative per-job paid-side-effect fence:

```text
false = no unresolved paid Flow generation exists for this job; a fresh
        generation is permitted only after local salvage, the exclusive lock,
        settled-editor verification, pre-clean, and stable empty verification
true  = a paid Flow generation may have been submitted but has not yet become
        six validated canonical local clips; automatic pre-clean, deletion,
        and Generate are forbidden
```

Legacy rows migrate with `flow_generation_unresolved = false`. The SQLite column is non-null with a false/zero default. For a genuinely fresh `TTS_READY` job, one `patch_job()` transaction persists `status=FLOW_GENERATING`, `checkpoint=TTS_READY`, and `flow_generation_unresolved=true` after safe empty verification but before the provider clicks Generate. This intentionally prefers a false-positive unresolved fence if the process dies before the click over the risk of duplicate paid generation.

`flow_cleanup_unresolved` is durable per-job recovery/audit state:

```text
true  = FLOW_READY/local canonical clips are durable, but remote empty-state
        cleanup has not yet been observably confirmed successful; this includes
        pending, interrupted, or failed cleanup
false = post-job Flow cleanup completed and refresh/reload verified zero product clips
```

The cleanup flag never decides whether the next fresh job pre-cleans. Fresh jobs always pre-clean; an unresolved-generation job follows the separate no-cleanup reconciliation rule.

Legacy rows migrate with `flow_cleanup_unresolved = false`. The SQLite column is non-null with a false/zero default. The transition to `FLOW_READY` writes status, checkpoint, progress/current step, `flow_generation_unresolved=false`, and `flow_cleanup_unresolved=true` in one `patch_job()` update/transaction.

## 7. Adaptive Six-Clip timing policy

### 7.1 Base visual duration

Google Flow still produces:

```text
6 clips × approximately 10 seconds = approximately 60 seconds of source visuals
```

The narration duration measured from the canonical `voice.mp3` determines whether Canva must slow the visuals.

### 7.2 Playback-speed calculation

Let:

```text
D = measured narration duration in seconds
B = 60.0 seconds base six-clip visual duration
```

Then:

```text
if D <= B:
    canva_playback_speed = 1.0
    target_final_duration_seconds = B
else:
    canva_playback_speed = B / D
    target_final_duration_seconds = D
```

Examples:

| Narration | Required speed | Target final duration |
| ---: | ---: | ---: |
| 55.0 s | 1.000x | 60.0 s |
| 60.0 s | 1.000x | 60.0 s |
| 63.0 s | 0.952x | 63.0 s |
| 66.0 s | 0.909x | 66.0 s |
| 70.0 s | 0.857x | 70.0 s |

### 7.3 Safety floor

Do not slow six clips without limit. MVP defines a configurable product safety floor:

```text
cloud_agent_canva_min_playback_speed = 0.85
```

The value is a product-quality policy, not Canva's technical minimum.

After TTS is created and measured:

```text
required speed >= configured minimum
→ continue to Google Flow

required speed < configured minimum
→ stop before consuming Google Flow generation credit
→ persist an actionable validation failure
```

Recommended error code:

```text
NARRATION_TOO_LONG_FOR_SIX_CLIP
```

The error tells the user to shorten the script, reduce Target Words, select a faster Voice Rate, or use a future long-video mode.

With the initial `0.85x` policy, the practical narration ceiling is approximately 70.6 seconds. This ceiling is configuration-driven and must not be reintroduced as a hard-coded `> 60` or `> 62` rule.

### 7.4 Why the policy is based on real audio

Word count alone is not authoritative because duration varies with:

- language;
- selected provider and voice;
- punctuation and pauses;
- Voice Rate;
- numbers, abbreviations, and mixed-language text.

Target Words may provide UI guidance, but the Cloud Agent timing gate uses the measured production audio.

## 8. Queue, lease, heartbeat and restart recovery

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
→ re-probe voice.mp3 when TTS_READY or later
→ verify persisted/calculated timing policy
→ resume at next safe step
```

A restart must never cause a successful TTS or Flow step to be repeated merely to recover timing information.

## 9. File layout

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
        │   ├── clip_06.mp4
        │   ├── downloads/ containing temporary bulk ZIP files
        │   ├── staging/ containing validated pre-canonical artifacts
        │   └── quarantine/ containing incomplete/invalid crash artifacts
        ├── screenshots/
        ├── logs/
        └── final/
            └── final.mp4
```

`prepare(job_id)` creates directories and returns deterministic paths. It does **not** create empty placeholder media files. `write_inputs(...)` writes script/Master Prompt after validated input exists.

## 10. Local + Session Preflight

Immediately after the user presses `Start Auto Production`, persist a job and queue it. Before TTS or any paid generation work, the worker runs Preflight.

Required checks:

1. Worker is executing and has a valid worker identity/heartbeat.
2. Job storage root exists, is writable, and satisfies configured minimum free space.
3. Google Flow session is usable by opening the real service.
4. Canva session is usable by opening the real service.

Flow/Canva checks must verify authenticated functionality, not just Cookie existence.

### 10.1 Session states

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

### 10.2 Auto Re-login

If a service session expired but an already-authenticated Google account is offered by the service:

```text
SESSION_EXPIRED
→ AUTO_RELOGIN
→ Continue with Google / choose saved account
→ verify service page
→ READY
```

Do not store a Google password in application config/database.

### 10.3 Human Required boundary

Never bypass:

- password challenge;
- CAPTCHA;
- 2FA;
- Google Prompt;
- Verify-it's-you;
- equivalent security challenge.

Use:

```text
HUMAN_REQUIRED
```

The worker preserves `checkpoint`, current evidence, and the browser state needed for manual recovery when safe.

### 10.4 Browser/noVNC model

Production Playwright runs in a virtual display environment compatible with noVNC. The production deployment uses a headed browser on Xvfb so `Open Browser` can display the same server-side browser session during `HUMAN_REQUIRED`.

`cloud_agent_browser_headless` remains configurable for tests/development, but the Ubuntu human-recovery deployment uses headed mode.

Persistent profiles are configurable server-side paths. Recommended production locations are outside the repository, for example:

```text
/var/lib/videosturbo/browser-profiles/google-flow
/var/lib/videosturbo/browser-profiles/canva
```

Only one process may use a service profile at a time. Browser profile access must use a process-safe lock/lease, not only a Python `threading.Lock`, because API and worker are separate processes.

The normal job worker owns profiles during production. Manual API session checks must acquire the same service lock; if the service is busy, they return a bounded busy response rather than opening a competing persistent context.

## 11. Workflow

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

The plan remains six chronological visual sections. It is not converted into a variable-N clip plan for this MVP.

### Step 2 — Start and Preflight

```text
QUEUED
→ PREFLIGHT
→ local health + Flow + Canva checks
→ PREFLIGHT_PASSED
→ TTS_GENERATING
```

No TTS/Flow credit should be consumed before Preflight passes.

### Step 3 — Production TTS once + exact timing

```text
script
→ existing voice.tts API-backed routing
→ canonical voice.mp3
→ ffprobe validation
→ read decimal audio_duration_seconds
→ calculate canva_playback_speed
→ apply safety-floor gate
→ persist timing values
→ TTS_READY checkpoint
```

Validate:

- file exists;
- meaningful non-zero size;
- readable audio stream;
- audio codec readable;
- finite positive duration;
- required Canva playback speed is at or above configured minimum.

Do not reject merely because narration exceeds 60 or 62 seconds.

Do not create a second production TTS after this point. `voice.mp3` is reused by Canva and after restart/resume.

### Step 4 — Local Flow salvage before any new paid generation

The safety-floor gate from Step 3 must pass **before** Flow generation. A job at `TTS_READY` must first try to recover already-paid local output from the crash window between bulk download/materialization and durable `FLOW_READY`.

Local recovery has priority even when `flow_generation_unresolved=true`. If canonical, ZIP, or staging recovery reconstructs six valid canonical videos, no remote Generate is permitted; the workflow acquires the Flow workspace lock, atomically commits `FLOW_READY` with `flow_generation_unresolved=false` and `flow_cleanup_unresolved=true`, attempts the existing remote cleanup, then continues to Canva.

Recovery priority is mandatory:

1. Validate the complete canonical set `clip_01.mp4` … `clip_06.mp4` with the existing full video validation policy. If all six pass, do not generate.
2. If canonical files are partial or invalid, move that set to the job-local quarantine area. Do not expose it as `FLOW_READY`.
3. Inspect surviving job-local bulk ZIP and staging artifacts before deleting them. Safely reprocess the ZIP when present; otherwise validate a complete staged semantic set `clip 1` … `clip 6`.
4. If salvage reconstructs one complete valid set, materialize the six canonical files and do not generate.
5. Only when canonical, ZIP, and staging recovery all fail to produce a complete valid set may the workflow acquire the workspace and permit a new paid generation.

ZIP/staging inspection must never trust archive order or member paths. Invalid temporary state remains job-local and is quarantined or removed only after salvage has been attempted.

When a complete local set is recovered:

```text
acquire exclusive google_flow workspace/profile lock
→ atomically persist checkpoint=FLOW_READY and flow_cleanup_unresolved=true
→ attempt remote cleanup while the same lock remains held
→ refresh/reload and verify zero product clips
→ only then persist flow_cleanup_unresolved=false
→ release lock
→ honor a newly-arrived pause/cancel boundary
→ continue toward Canva when not paused/cancelled
```

Recovery from local artifacts never invokes Flow Generate.

### Step 5 — Dedicated Flow workspace generation, semantic ordering and bulk ZIP

Before a genuinely new paid generation:

```text
check Flow session with the normal bounded session-check policy
→ acquire the long-lived exclusive google_flow workspace/profile lock
→ open the configured dedicated project
→ prove the editor is settled and actionable within the editor-readiness timeout
→ inspect existing product clips
→ delete every existing product clip when any exist
→ refresh/reload
→ prove a settled, stable zero-product inventory across multiple polls
→ atomically persist status=FLOW_GENERATING, checkpoint=TTS_READY,
  flow_generation_unresolved=true
→ only then submit generation exactly once
```

The next fresh job always performs this pre-clean regardless of any prior job's `flow_cleanup_unresolved` value. Failure to prove the settled editor or stable zero-product state raises `FlowWorkspaceVerificationError` and stops before a new paid generation. The paid fence write happens after safe pre-clean/empty verification and before the click; the provider never writes SQLite.

Live authenticated Flow evidence establishes this settled-editor barrier without coordinate selectors:

```text
document.readyState == complete
exactly one Agent button is visible and enabled
exactly one composer editable associated with Agent is visible
the All media / สื่อทั้งหมด control is uniquely visible
data-testid="virtuoso-item-list" is uniquely visible
no visible aria-busy=true element
no visible progressbar
the observable media-card inventory remains unchanged for at least 3 polls
```

The virtualized inventory uses the observed `virtuoso-item-list` boundary and observable product-card descendants. A single poll with zero checkboxes, zero cards, or transient empty text is never proof of emptiness. Empty verification requires the whole settled barrier plus a zero inventory that remains stable across at least three polls. Loading/hydration resets the stability count. Editor readiness has its own short bounded timeout; it does not borrow the 1,800-second generation timeout.

The default editor-readiness timeout is 120 seconds and is an independent `GoogleFlowClient` constructor policy. The production factory uses that policy from the existing composition root; it does not add a second config loader. Tests may inject shorter values. Existing generation/reconciliation waits retain the separate 1,800-second generation policy.

At `TTS_READY` with `flow_generation_unresolved=true`, the workflow enters reconciliation under the same exclusive workspace lock. Reconciliation may inspect and wait for the existing remote request, semantically rename exactly six resolved current-job clips, bulk-download once, and run the existing safe local validation/materialization. It must never pre-clean, delete, or click Generate. If exactly six results cannot be reconciled safely, the workflow persists:

```text
status=HUMAN_REQUIRED
checkpoint=TTS_READY
flow_generation_unresolved=true
error_code=FLOW_GENERATION_RECONCILIATION_REQUIRED
```

Partial remote output is retained. A later paid retry requires explicit authorization after remote state reconciliation.

Generation then performs:

```text
Agent Mode
→ paste Master Prompt
→ Generate six videos
→ wait for all six videos to complete through observable state
→ send Agent instruction: "เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ"
→ wait for Agent completion
→ refresh/reload
→ verify exactly one each: clip 1, clip 2, clip 3, clip 4, clip 5, clip 6
→ trigger Flow's bulk Download Product Clips action
→ receive one ZIP archive
```

Completion order is never semantic order. Only the verified names `clip 1` … `clip 6` determine canonical ordering.

The bulk archive is processed with Python `zipfile` in a job-local temporary staging area. Reject absolute paths, parent traversal, symlinks, encrypted/unsafe entries, missing names, duplicate sequence numbers, ambiguous names, unexpected extra video entries, and invalid video media. Map:

```text
clip 1 → clip_01.mp4
clip 2 → clip_02.mp4
clip 3 → clip_03.mp4
clip 4 → clip_04.mp4
clip 5 → clip_05.mp4
clip 6 → clip_06.mp4
```

All six staged videos must pass existing size/stream/duration/resolution validation before any canonical destination is materialized. Archive order is ignored. An unsafe, incomplete, duplicate, ambiguous, or invalid archive raises `FlowArchiveValidationError` before `FLOW_READY`.

After six canonical local files are durable and validated, but while the same workspace lock remains held:

```text
one SQLite update:
    status=FLOW_READY
    checkpoint=FLOW_READY
    flow_generation_unresolved=false
    flow_cleanup_unresolved=true
→ attempt remote delete-all cleanup before honoring pause/cancel
→ refresh/reload
→ verify zero product clips
→ success: set flow_cleanup_unresolved=false
→ failure/interruption: retain true, do not roll back FLOW_READY, continue to Canva
→ release workspace lock
```

`GoogleFlowClient` never writes SQLite. `CloudAgentWorkflow` owns the checkpoint and boolean transitions; `CloudJobStore` owns compatible schema migration and atomic persistence. Do not rely on a fixed `sleep` as the only readiness signal.

### Step 6 — Canva re-check and Adaptive Six-Clip final assembly

Before Canva:

```text
check Canva session
→ repair if safe
→ verify READY
```

MVP prefers a prepared Canva template.

Sequence:

```text
upload six validated Flow clips
→ upload canonical voice.mp3
→ order clips 1→6
→ keep straight cuts
→ if canva_playback_speed < 1.0, apply the same custom playback speed to all six clips
→ verify the Canva timeline changed as expected
→ mute source-video audio when narration is primary
→ place narration from time 0
→ trim the final visual end only when required to correct UI rounding/overshoot
→ generate Auto Captions
→ use template caption style
→ export MP4 1080p
→ download final.mp4
```

Uniform playback adjustment is preferred over slowing only the last clip because it preserves consistent motion and chronology across the video.

The Cloud Agent must not use coordinate-only clicks as its primary Canva automation strategy. Prefer role/text/accessible-label/input selectors plus observable post-action verification.

If Canva changes UI, the playback control cannot be located reliably, or the resulting timeline cannot be verified, do not guess. Preserve `checkpoint=FLOW_READY` and transition to `HUMAN_REQUIRED` when manual recovery is appropriate.

### Step 7 — Canva Playback Automation Spike gate

Before the production Canva adapter is considered implementation-ready, run a real Canva Editor spike on the target workflow and demonstrate all of the following:

1. Playwright can select an uploaded video clip reliably.
2. Playwright can open the Playback control without coordinate-only automation.
3. A custom playback speed such as approximately `0.95x` can be entered/applied.
4. The UI exposes an observable result sufficient to verify that duration/playback changed.
5. The action can be repeated across all six clips.
6. The final clip/timeline can be trimmed or otherwise bounded so the export ends at the narration-derived target duration within configured tolerance.
7. The same operation works in the headed Xvfb/noVNC production-style browser environment.

If the spike cannot satisfy these requirements reliably, stop and revise the assembly strategy before implementing deeper Canva automation. Do not silently move clip concatenation back into VideosTurbo without an explicit design revision.

### Step 8 — Final Validation

A browser download-complete event is not enough.

Validate:

- file exists;
- configured minimum size;
- readable video stream;
- readable audio stream;
- readable duration;
- expected resolution when configured (MVP target 1080x1920);
- ffprobe can read the file without corruption error;
- final duration is within configured tolerance of `target_final_duration_seconds`;
- final duration is not shorter than the narration by more than the configured tolerance.

The target is:

```text
target_final_duration_seconds = max(60.0, audio_duration_seconds)
```

Only then persist:

```text
FINAL_VALIDATED
```

### Step 9 — Cleanup and completion

Only after `FINAL_VALIDATED`:

- delete local Flow source clips according to retention policy; remote Flow product clips were already cleaned immediately after durable `FLOW_READY`;
- delete upload/browser/export temporary files according to policy;
- retain Script, Master Prompt, `voice.mp3`, `final.mp4`, and job metadata by default.

Then:

```text
COMPLETED
```

If Final Validation fails, source clips remain available for retry/debug.

## 12. Retry and Resume

Each external step has bounded retry. Default operational policy is 3 attempts, configurable.

Classify failures:

- transient network/browser timeout → retry;
- deterministic invalid artifact → `FAILED` unless a step-specific retry can fix only that artifact;
- inability to prove a settled actionable Flow editor or stable empty workspace before the paid fence → `FlowWorkspaceVerificationError`, fail the job without setting paid-generation reconciliation state;
- any Flow workspace verification failure after `flow_generation_unresolved=true` → preserve `checkpoint=TTS_READY` and the fence, transition to `HUMAN_REQUIRED` with `FLOW_GENERATION_RECONCILIATION_REQUIRED`, and do not let the typed failure terminate the Worker;
- unsafe, incomplete, duplicate, ambiguous, or invalid Flow ZIP before a paid fence → `FlowArchiveValidationError`, hard failure before `FLOW_READY`;
- the same archive failure after `flow_generation_unresolved=true` → preserve the fence and transition to reconciliation-required `HUMAN_REQUIRED`, never automatically generate again;
- post-`FLOW_READY` Flow cleanup failure/interruption → retain `FLOW_READY`, leave `flow_cleanup_unresolved=true`, do not propagate job failure, and continue toward Canva;
- narration requires playback below the configured safety floor → `FAILED` before Flow with `NARRATION_TOO_LONG_FOR_SIX_CLIP`;
- password/CAPTCHA/2FA/security challenge → `HUMAN_REQUIRED` immediately;
- Canva UI cannot be safely controlled/verified → `HUMAN_REQUIRED` when manual recovery is appropriate;
- user pause → preserve checkpoint and `PAUSED`;
- user cancel → `CANCELLED` at a safe boundary.

Resume must validate artifacts required by the checkpoint before skipping a paid step.

At `TTS_READY`, resume performs the canonical → ZIP/staging salvage priority from Workflow Step 4. A complete valid salvaged set becomes `FLOW_READY` without regeneration. Partial/invalid canonical materialization is quarantined before salvage. If salvage is unavailable and `flow_generation_unresolved=false`, the fresh fenced path may run. If salvage is unavailable and `flow_generation_unresolved=true`, only read-only remote reconciliation may run; pre-clean, delete, and Generate remain forbidden.

Example:

```text
checkpoint=FLOW_READY
voice.mp3 valid
six clips valid
→ re-probe voice.mp3
→ verify audio_duration_seconds / canva_playback_speed policy
→ re-check Canva
→ continue at Canva
```

Never regenerate TTS/Flow merely because Canva required human login or a worker/server restart occurred.

At `FLOW_READY`, resume validates the six canonical clips and never invokes Flow generation. A historical `flow_cleanup_unresolved=true` does not block Canva and does not control the next job's mandatory pre-clean.

Expected per-job `FlowWorkspaceVerificationError` and reconciliation failures are contained at the workflow boundary and converted into durable job state. They do not normally escape `CloudAgentWorker.run_once()` or rely on systemd restart for control flow.

## 13. API surface

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

- Cloud Agent enabled/disabled;
- worker last-seen timestamp / online status;
- storage writable status;
- free-space status.

Job detail responses should expose the measured narration duration, approved Canva playback speed, and target final duration so the UI can explain timing decisions without recomputing them client-side.

## 14. UI

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

Target Words remains guidance, not an authoritative duration gate.

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

- Job ID;
- current step;
- checkpoint;
- narration duration after TTS is ready;
- Canva playback speed when below `1.0x`;
- Pause / Resume / Cancel;
- Human Required reason/evidence link when safe;
- Job History.

For `NARRATION_TOO_LONG_FOR_SIX_CLIP`, show the measured duration, required playback speed, configured minimum speed, and actionable options such as shortening the script or increasing Voice Rate.

### Final result

- Preview
- Duration
- Resolution
- subtitle/caption status
- voice
- Download MP4

## 15. Configuration baseline

Cloud Agent settings live under `[app]`. Initial defaults/configuration include equivalents of:

```text
cloud_agent_enabled = false
cloud_agent_db_path = storage/cloud-agent.sqlite3
cloud_agent_worker_poll_seconds = 2
cloud_agent_worker_lease_seconds = 120
cloud_agent_worker_heartbeat_seconds = 10
cloud_agent_max_retries = 3
cloud_agent_min_free_disk_gb = 10
cloud_agent_tts_min_duration_seconds = 1
cloud_agent_canva_min_playback_speed = 0.85
cloud_agent_final_duration_tolerance_seconds = 1.0
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

The former v2.1 default:

```text
cloud_agent_tts_max_duration_seconds = 62
```

is obsolete under this design and must be removed/replaced during implementation rather than used to reject valid Adaptive Six-Clip narration.

`cloud_agent_tts_min_duration_seconds` is now a sanity floor for a valid generated audio artifact, not a target-duration preference.

Never commit real credentials, cookies, browser profiles, signed download URLs, or API keys.

## 16. Error evidence

Browser failures record:

- service;
- job id when applicable;
- current step;
- timestamp;
- current URL when safe;
- sanitized error message;
- screenshot when it does not expose credentials/security secrets.

Example:

```text
storage/jobs/<job_id>/screenshots/flow_generate_error.png
storage/jobs/<job_id>/screenshots/canva_playback_error.png
storage/jobs/<job_id>/logs/agent.log
```

## 17. Security boundaries

- No CAPTCHA/2FA/security-challenge bypass.
- No passwords in config/SQLite.
- API keys stay server-side.
- Browser profiles stay outside public static directories.
- Final-file endpoint must resolve the job-owned validated path; reject path traversal.
- noVNC must not be anonymous on the public internet.
- Nginx/TLS/authentication or equivalent access control is required for production exposure.

## 18. TDD and test strategy

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

CI/unit tests do not depend on a live Google Flow/Canva account. Provider adapters use deterministic local HTML fixtures/page-object tests for state detection.

Timing-policy unit tests must cover at least:

- 55 s → `1.0x`, target 60 s;
- 60 s → `1.0x`, target 60 s;
- 63 s → approximately `0.95238x`, target 63 s;
- 70 s → approximately `0.85714x`, target 70 s;
- duration requiring `<0.85x` → reject before Flow;
- decimal duration is not ceiled before calculation;
- resume from `TTS_READY` re-probes/validates the existing audio instead of creating TTS again;
- resume from `FLOW_READY` does not recreate TTS or Flow.

Dedicated Flow workspace tests must cover at least:

- compatible SQLite migration/default/round-trip for `flow_cleanup_unresolved`;
- compatible SQLite migration/default/round-trip for `flow_generation_unresolved`;
- the generation fence is durably persisted before the provider can click Generate;
- a crash immediately before or after the Generate click resumes without a second Generate;
- `TTS_READY + flow_generation_unresolved=true` never pre-cleans, deletes, or generates;
- six existing remote results reconcile through rename/download without a new Generate;
- partial remote results are retained and transition to reconciliation-required state without cleanup or generation;
- a workspace verification error before the fence fails safely with the fence false;
- a workspace verification error after the fence becomes `HUMAN_REQUIRED` with the fence true;
- an expected typed Flow workspace failure does not escape and terminate the Worker;
- loading/transient zero inventory cannot satisfy empty verification;
- a settled actionable editor with a stable zero inventory may satisfy the pre-generation gate;
- stale remote product clips are deleted and refreshed zero state is verified before Generate;
- failure to prove empty state prevents Generate;
- out-of-order completion is not used for semantic ordering;
- the exact Agent rename instruction is submitted and exactly one `clip 1` … `clip 6` is verified after refresh;
- the bulk Download Product Clips action yields one ZIP;
- ZIP-slip/absolute/symlink/encrypted entries are rejected;
- missing, duplicate, ambiguous, extra, or invalid video entries raise `FlowArchiveValidationError`;
- shuffled ZIP members materialize canonical `clip_01.mp4` … `clip_06.mp4` by semantic name;
- canonical destinations remain unavailable until the full staged set validates;
- complete canonical files at `TTS_READY` recover to `FLOW_READY` with zero generation calls;
- partial canonical files plus a valid surviving ZIP/staged set reconstruct all six with zero generation calls;
- unrecoverable partial state is quarantined, then the workspace is pre-cleaned before a new generation;
- `FLOW_READY + flow_generation_unresolved=false + flow_cleanup_unresolved=true` are persisted atomically before remote cleanup;
- cleanup success verifies empty state before changing unresolved to false;
- cleanup failure retains `FLOW_READY`, local clips, and unresolved=true while Canva continues;
- pause/cancel arriving at the Flow boundary is honored only after the post-`FLOW_READY` cleanup attempt;
- a second job cannot enter the workspace while the first owns the lock and begins only after its own pre-clean verifies empty;
- restart at `FLOW_READY` never invokes Flow generation.

Real third-party behavior is verified only at explicit smoke/spike gates.

## 19. MVP acceptance criteria

MVP is complete only when all are demonstrated:

1. Script can be generated, edited and saved using existing LLM behavior.
2. Existing six-clip logic produces/persists six prompt records and Master Prompt.
3. User can manually check Flow/Canva sessions.
4. Start always performs local + session Preflight before TTS.
5. Ordinary expired session can Auto Re-login when an already-authenticated account is offered.
6. CAPTCHA/2FA/verification becomes `HUMAN_REQUIRED` without bypass.
7. noVNC shows the server-side browser for human recovery.
8. TTS uses existing API-backed voice routing and creates the canonical production `voice.mp3` once.
9. Exact decimal narration duration is measured and persisted without an early `ceil()` timing calculation.
10. Narration longer than 60/62 seconds is not rejected solely for exceeding the old fixed timeline.
11. Required Canva playback speed is calculated from real narration duration and checked before Flow credit is consumed.
12. Narration requiring playback below the configured minimum is rejected before Flow with an actionable error.
13. Each fresh Flow job acquires the dedicated shared workspace, proves the editor settled, removes stale product clips, refreshes, and proves a stable zero inventory before generation.
14. `flow_generation_unresolved=true` is durable before the paid Generate click; restart cannot pre-clean, delete, or generate while the fence remains true.
15. Existing remote results reconcile without a new Generate, while unsafe partial/ambiguous state becomes `HUMAN_REQUIRED` and remains untouched.
16. Flow generates six clips, renames them semantically with the approved Agent instruction, verifies exactly one `clip 1` … `clip 6`, and bulk-downloads one ZIP.
17. Secure ZIP processing ignores completion/archive order, rejects unsafe or ambiguous content, and materializes exactly six validated canonical clips.
18. `FLOW_READY + flow_generation_unresolved=false + flow_cleanup_unresolved=true` are durable before remote cleanup; cleanup failure cannot invalidate local artifacts or trigger regeneration.
19. Complete canonical or salvageable ZIP/staging artifacts at `TTS_READY` recover without another paid Flow generation.
20. The exclusive workspace lock prevents a second job from entering until the current owner finishes its cleanup attempt and releases ownership.
21. Canva uploads and arranges six clips plus the same canonical narration file.
22. When narration exceeds 60 seconds within policy, Canva applies uniform playback adjustment across all six clips.
23. Canva playback automation has passed the real Editor spike including observable post-action verification.
24. Canva generates captions and exports MP4.
25. `final.mp4` passes stream/resolution/size and narration-derived duration validation before local source cleanup.
26. User can Pause/Resume/Cancel at defined safe boundaries; post-Flow cleanup is attempted before a newly arrived pause/cancel is honored.
27. Resume validates checkpoint, fence, and salvage artifacts and does not repeat successful paid steps unnecessarily.
28. Worker lease/heartbeat prevents duplicate execution and exposes Cloud Agent online status.
29. Expected per-job Flow workspace errors become durable job state without killing the Worker.
30. Closing the local browser/computer does not stop production.
31. Worker/server restart preserves the job and resumes from a safe checkpoint.
32. WebUI shows history/status/timing/final Preview/Download.
33. Legacy stock/render code remains available until this E2E gate passes.

## 20. Real Ubuntu smoke gate

Before declaring production-ready or starting legacy cleanup, demonstrate on Ubuntu 24.04:

```text
Generate/Edit Script
→ SixClipPlan + Master Prompt
→ Start
→ Preflight
→ one production TTS + exact duration
→ timing policy / playback-speed calculation
→ Flow session re-check
→ acquire dedicated Flow workspace lock
→ pre-clean + verify zero product clips
→ 6 Flow clips + semantic Agent rename
→ bulk ZIP + safe local validation/materialization
→ durable FLOW_READY + flow_cleanup_unresolved=true
→ remote cleanup + verify zero clips
→ Canva session re-check
→ upload/arrange six clips
→ adaptive playback adjustment when required
→ narration/captions
→ export/download
→ Final Validation against target duration
→ local source cleanup after final validation only
→ COMPLETED
```

Also verify:

- a narration around 63 seconds completes through Adaptive Six-Clip without a second TTS;
- a narration beyond the configured playback safety floor stops before Flow generation;
- Auto Re-login scenario;
- HUMAN_REQUIRED + noVNC + Resume scenario;
- local browser/computer closed during production;
- worker/server restart at `TTS_READY` and `FLOW_READY`;
- crash-window recovery from complete canonical files and from partial canonical plus surviving valid ZIP/staging artifacts;
- no duplicate TTS/Flow after resume;
- a second job waits for workspace ownership and performs its own verified pre-clean;
- a controlled post-`FLOW_READY` cleanup failure leaves unresolved=true while Canva continues;
- final exported duration does not truncate the narration outside configured tolerance.

## 21. Legacy cleanup gate

Legacy cleanup is a separate follow-up phase after the smoke gate passes. Remove one category at a time, with regression tests and commits between categories.

Candidates may include:

- Pexels/Pixabay/Coverr paths no longer used by retained features.
- material-type/mixed image-video controls.
- old six-media Upload/URL controls from the main production path.
- Ken Burns/image processing used only by the removed path.
- legacy local render code with no retained references.
- dependencies proven unused after source cleanup.

Do not remove Music Batch or other retained features merely because the new Create Video path no longer calls them.

## 22. Deferred long-video mode

MVP Adaptive Six-Clip is intentionally optimized for narration near one minute. It is not a claim that six clips should be stretched to arbitrary length.

A later long-video phase may introduce Dynamic Clip Timeline with:

- clip counts derived from narration duration;
- batched LLM/Flow generation;
- per-clip durable progress;
- paginated UI;
- cost/time estimation;
- backward-compatible data versioning.

That phase requires its own design/implementation gate. Do not partially introduce variable clip counts into this MVP.

## 23. Final baseline principle

```text
VideosTurbo = Content + Control + Status
FastAPI = shared control contract
Cloud Agent Worker = durable workflow execution
SQLite = Cloud Agent durable jobs/checkpoints/leases/heartbeat/timing data
TTS = existing API-backed voice service; production audio created once
Google Flow = one dedicated exclusively locked workspace producing six temporary AI source clips through Playwright browser automation
Canva = playback adjustment + final assembly + narration + captions + export
FFprobe = local timing/media validation, not Cloud Agent final concatenation
Ubuntu Cloud Server = 24/7 execution environment
```

Product target:

> **ใส่หัวข้อ → ตรวจ Script → กด Start → ปิดคอม → Cloud Agent วัดเสียงจริงและปรับ Canva ให้เหมาะสม → กลับมารับ Final Video**
