# Cloud Video Agent — Master System Continuity Handoff

Source: `VideosTurbo_Cloud_Agent_Master_System_Handoff.md` supplied on 2026-08-25. This repository record preserves the operating contract needed to continue Task 14; the live working tree, SQLite state, and remote branch always override historical snapshots.

## Source-of-truth order

1. `/opt/VideosTurbo` working tree
2. VPS SQLite/job artifacts
3. `origin/feature/cloud-video-agent`
4. current docs under `docs/superpowers/`
5. supplied handoffs

## System model

FastAPI → `CloudJobStore`/SQLite + per-job storage → lease-owning Worker → `CloudAgentWorkflow` → canonical TTS → Google Flow → `FLOW_READY` → Canva assembly → server final validation → `FINAL_VALIDATED` → `COMPLETED`.

Status and checkpoint remain separate. Factory composition must reuse `config.app`; do not create a second FastAPI app, config loader, worker, or browser-profile implementation.

## Current Task 14 job

- Job: `7c76329b-c533-453d-8b2e-9533c2642153`
- Durable checkpoint: `FLOW_READY`
- Canonical narration: `voice.mp3`, 63.936 seconds
- Canva playback factor: `0.9384384384384384`
- Six canonical local Flow clips are already durable.
- New TTS calls: **0**. New Flow Generate requests: **0**. Paid Attempt #2: **not authorized**.
- `flow_generation_unresolved=false` must remain so; `flow_cleanup_unresolved=true` never permits regeneration.

## Hard invariants

- Use Canva, never a silent FFmpeg final-assembly fallback.
- Final output is MP4 1080x1920; Flow sources remain native 9:16 (currently 720x1280), with no resizing/upscaling solely to meet final dimensions.
- Browser profiles are private persistent resources; do not clone/delete/expose them or credentials/cookies/tokens/signed URLs.
- Google/Canva CAPTCHA, 2FA, device confirmation, OAuth/account-selection, or interactive login is `HUMAN_REQUIRED`.
- Keep noVNC/VNC loopback-only. Do not start Task 15 before Task 14 acceptance.

## Current Canva acceptance path

Configured Canva workspace only:

```text
open design → pre-clean Uploads/Videos → timeline zero
→ upload existing six clips + existing voice
→ click semantic cards clip_01 … clip_06 and prove each timeline +1
→ existing playback/mute/narration/trim/captions/export
→ server validation → FINAL_VALIDATED → post-clean Videos → COMPLETED
```

Video cleanup is card-scoped and fail-closed: fresh hover, fresh overlapping `Show details for “<filename>”` control, fresh hit-tested geometry, verify `Details`/`Download`/`Move`/`Move to Trash`, click Trash immediately, then re-query. Never use fixed coordinates or delete Images/another Canva project. Scope must not silently broaden beyond the dedicated VideosTurbo workspace.

### Validated Audio cleanup contract (2026-08-26)

Before uploading canonical narration, delete only stale cards named
`Apply audio: voice.mp3` from the live `Uploads -> Audio` panel. For each fresh,
visible card: hover its current card box; select the generic `Show details` button
only when its current hit-tested box overlaps that card; wait for exactly one
visible `button[aria-label="Delete"]`; click it once; then reopen `Audio` and
require the exact managed-card count to decrease by one. After the last deletion,
reload once and wait for Audio hydration; zero is accepted only when it remains
zero after hydration. An initial zero must wait through the bounded five-second
Audio-card hydration window before it is treated as empty. This is distinct from
Video's `Move to Trash` action.

## Development and stopping rule

All production behavior follows RED → observed intended failure → smallest GREEN → focused/full verification → commit → push → CI → live evidence.

Continue resolving routine, reversible implementation and verification failures autonomously: root cause → minimal RED-to-GREEN fix → focused/full verification → CI → live evidence. Stop only for interactive authentication, CAPTCHA/2FA/device/OAuth interaction, destructive or irreversible production changes, a public-security change, or a genuine architecture decision requiring the operator. The former five-attempt reporting ceiling was explicitly withdrawn on 2026-08-25.

## Immediate continuation

Continue the active Canva cleanup/assembly work from actual state. Do not repeat settled Flow investigations or regenerate assets. Keep PR #4 Draft until Task 14 Gate F has fresh evidence.
