# Canva Job Workspace Persistence

## Problem

`cloud_agent_canva_template_url` is a Canva create-design URL. Opening it creates
a new design. The current CloudJob has no persisted Canva design reference, so a
worker retry at `FLOW_READY` opens another new design rather than the workspace
that already contains that job's uploads and timeline state.

The current Audio insertion selector also conflates two different states:
it treats every result other than exactly one `Apply audio: voice.mp3` control as
the same ambiguous failure. It does not durably record which observable state was
seen in the active design.

## Goals

- Create at most one Canva design for each CloudJob.
- Persist the resulting editor URL before any mutable workspace action.
- Resume a job in its persisted design, never at the create-design URL again.
- Keep one browser context for a job's Canva assembly and post-final cleanup.
- Make media cleanup and insertion operate only on the current job's six canonical
  video names and canonical `voice.mp3`.
- Fail closed with a typed Canva UI verification error when the active Audio panel
  cannot prove exactly one narration card.
- Record sanitized, durable UI evidence for an Audio-card failure: the active
  panel state and count only, never cookies, tokens, signed URLs, or profile paths.

## Non-goals

- Do not change Flow, TTS, paid-operation budgets, or canonical artifacts.
- Do not reuse one Canva design across different CloudJobs.
- Do not delete unrelated Canva media.
- Do not use coordinates or bypass Canva UI state verification.

## Data model and migration

Add these compatible `cloud_agent_jobs` columns and matching `CloudJobRecord`
fields:

- `canva_design_url TEXT NOT NULL DEFAULT ''`: canonical editor URL for this job.
- `canva_audio_card_count INTEGER NOT NULL DEFAULT -1`: last observable number of
  exact canonical narration cards in the active Audio panel. `-1` means not yet
  observed.

The existing `CloudJobStore` owns migration and persistence. A record created
before this change gets empty URL and `-1` count. No separate config loader or
workspace table is introduced.

## Canva session lifecycle

1. At the first Canva assembly attempt, open the configured create-design URL.
2. Wait for the authenticated editor's observable ready state.
3. Read the resulting editor URL. It must be an HTTPS Canva `/design/<id>/edit`
   URL and must not be the create-design URL.
4. Persist `canva_design_url` immediately in `CloudJobStore` before cleanup,
   upload, or timeline changes.
5. For every retry/resume, open only `canva_design_url` and verify it is an
   editor-ready URL before acting.
6. If the URL is missing for an old job, create and persist one workspace once.
   This is safe only before any Canva assembly state is treated as durable.

The workflow owns the transition. `CanvaAssemblyClient` reports the resolved
editor URL through a narrow session interface; it must not write SQLite.

## Audio contract

Within the active job design and same persistent context:

1. Open Uploads -> Audio using the visible tab and its `aria-controls` panel.
2. Before upload, delete every stale exact `Apply audio: voice.mp3` card using
   the card-scoped generic `Show details` overlay and the popup action
   `button[aria-label="Delete"]`. Wait for the popup action, verify each live
   panel count decreases by one, then reload once and require hydrated zero.
3. Count exact `Apply audio: voice.mp3` controls only within that panel.
4. Persist the sanitized count on the job before selecting the card.
5. Continue only for count `1`; click that panel-scoped card and verify the
   timeline postcondition.
6. For `0` or `>1`, raise `CanvaUIVerificationError` that includes the count but
   no sensitive data. The worker preserves `FLOW_READY` and local source clips.

This fixes observability first. Cleanup may delete only exact managed names, but
does not treat missing or ambiguous Audio cards as successful narration insertion.

## Error and resume semantics

- A Canva failure never changes `FLOW_READY`, never regenerates Flow, and never
  regenerates TTS.
- `CANVA_UPLOADING` remains retryable. A later worker opens the same persisted
  design and re-checks UI state.
- An existing job with no persisted design URL may create one once on the next
  assembly attempt. New jobs always persist it before mutations.
- Control boundaries continue to be checked by `CloudAgentWorkflow`.

## Tests and verification

RED tests must prove:

1. First assembly persists the resolved editor URL before cleanup/upload.
2. Resume uses the persisted URL and does not open the create-design URL.
3. A non-editor or create URL returned after navigation is rejected.
4. Audio `0` and `>1` cases persist only a sanitized count and fail closed.
5. Audio `1` uses the scoped panel card and verifies one timeline addition.
6. Existing FLOW_READY resume has zero additional TTS/Flow calls.

Verification gates: focused Canva and workflow tests, full Cloud Agent regression,
Ruff on all modified files, then one controlled real resume of the existing job.
