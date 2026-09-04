# Cloud Agent Flow Recovery, Canva Restart, and Stall Watchdog Design

## Goal

Keep the normal Cloud Agent production path unchanged when Google Flow creates
all six clips and Canva completes normally. When one Flow clip fails, recover
only that clip with the exact original segment prompt. When Canva stalls,
restart only Canva. When any Job makes no meaningful progress for one hour,
stop its execution, delete the Job and local artifacts, do not create a new
Job, and retain a safe WebUI notification explaining where it stopped.

This is an additive recovery layer around the existing checkpointed workflow.
Research, script generation, TTS, Flow's successful batch path, Canva's
audio-first contract, final validation, the completed-video library, and the
event-driven WebUI remain authoritative in their current boundaries.

## Accepted Product Decisions

- Google Flow still receives one normal six-clip batch request first.
- A visible failed-output card is recognized immediately; the Worker does not
  wait for the existing 30-minute generation timeout after failure is proven.
- Flow Agent chat is not a source of truth. Actual downloaded project archives
  and validated local media are authoritative.
- Automatic Flow recovery is allowed only when exactly one missing clip can be
  identified unambiguously and the other five clips are valid and uniquely
  mapped to their original `CLIP N` numbers.
- The missing clip uses the exact `video_prompt` from the matching
  `job.clip_plan.segments` entry. The wrapper may constrain count and name, but
  must not rewrite that prompt.
- The missing clip may be submitted at most two additional times after the
  original six-clip batch.
- After a targeted replacement, the system downloads the whole Flow Project
  archive again. A newly downloaded, complete archive is preferred over mixing
  files from different downloads.
- A validated merge of the first five clips and the later replacement is a
  fallback only when the later Flow download contains only the new clip.
- Canva restarts after 20 minutes without meaningful Canva progress. It reuses
  the existing TTS audio and six validated Flow clips and may restart at most
  four additional times after the initial Canva attempt.
- Start-at-zero checks for narration and the first video remain best-effort,
  non-mutating observations. An unobservable position logs a warning and does
  not fail the attempt.
- Caption presence remains mandatory before export.
- A Job with no meaningful progress for one hour is stopped and deleted. It is
  not requeued and no replacement Job is created.
- Automatic deletion covers the SQLite Job and its local Job directory only.
  Remote Google Flow and Canva projects are not automatically deleted.
- The WebUI does not resume two-second Job polling. Worker-originated events and
  one reconciliation read on page load or SSE reconnect remain the update path.

## Scope Boundaries

### In scope

- Partial Flow inventory and failed-card detection.
- Semantic mapping of five surviving clips and one missing index.
- Two fenced, targeted replacement submissions.
- Fresh full-project archive validation and a strictly validated partial-archive
  merge fallback.
- Durable recovery counters and meaningful-progress timestamps.
- Canva attempt restart without repeating TTS or Flow.
- A Worker supervisor that can terminate one Job's browser automation before
  retrying or deleting it.
- Durable incident notices that outlive a deleted Job.

### Out of scope

- Creating all six Flow clips individually on the normal path.
- Asking the Agent to invent or rewrite a missing prompt.
- Recovering two or more missing clips automatically.
- Renumbering survivors to close a missing-number gap.
- Deleting remote Flow or Canva data.
- Changing Research, TTS providers, video-library media serving, or final video
  validation requirements.
- Replacing SQLite, the existing queue, or SSE with a message broker.

## Architecture

The recommended architecture combines deterministic provider recovery with an
isolated per-Job execution process:

```text
Worker supervisor claims Job and owns lease
        |
        +--> child process builds and runs workflow/browser automation
        |        |
        |        +--> durable Job state and meaningful-progress milestones
        |        +--> existing non-blocking Job events
        |        `--> small progress signal to supervisor
        |
        +--> Flow failure: child performs bounded one-clip recovery
        +--> Canva idle 20m: supervisor stops child, starts next Canva attempt
        `--> any-stage idle 1h: supervisor stops child, deletes Job locally,
                               persists incident, never requeues
```

The supervisor must not share an already-created Playwright browser, workflow
object, dispatcher thread, or SQLite connection with a child. The child entry
point receives only safe primitive identifiers, builds its own workflow inside
the child, and opens provider resources there. The parent owns claiming, lease
renewal, attempt deadlines, termination, and terminal cleanup.

The supervisor waits on child exit or progress signals with a deadline. It does
not need a two-second database polling loop. On process or Worker restart it
performs one durable reconciliation read before deciding the next action.

A narrow `ProgressReporter` boundary is injected into workflow/provider
operations. A milestone is recorded only after its observable postcondition
passes. The reporter commits the new timestamp and milestone identifier to
SQLite, then sends a best-effort wake-up signal to the supervisor. Timestamp-
only writes do not emit WebUI progress events; user-visible status continues to
follow the existing status/checkpoint/current-step/progress projection.

Termination is process-group scoped: request graceful termination, allow a
short bounded grace period, then force-kill that Job's process group if needed.
The supervisor may retry or delete only after it has confirmed the old child is
not running. Merely changing SQLite status while browser automation continues
is forbidden because the old attempt could still spend credits or write files.

## Durable State

SQLite remains the source of truth. Add compatible fields to the Job record:

- `last_progress_at`: UTC timestamp of the latest meaningful milestone;
- `last_progress_milestone`: a narrow non-sensitive milestone identifier used
  to prevent repeated observation of the same state from refreshing time;
- `stage_started_at`: UTC timestamp for the active recovery-capable stage;
- `flow_recovery_attempts`: number of targeted replacement submissions already
  reserved, from `0` through `2`;
- `flow_missing_clip_index`: `0` when unknown, otherwise `1` through `6`;
- `flow_recovery_state`: a narrow state such as `NONE`, `INVENTORY_PENDING`,
  `READY_TO_SUBMIT`, `SUBMISSION_UNRESOLVED`, or `VERIFICATION_PENDING`;
- `flow_recovery_baseline`: non-sensitive digest of the accepted five-clip
  inventory, never raw provider content;
- `canva_restart_attempts`: number of additional Canva attempts already
  reserved, from `0` through `4`; and
- `canva_attempt_started_at`: UTC timestamp of the current Canva attempt.

The existing `flow_generation_unresolved` fence continues to protect the
original paid six-clip batch. Targeted recovery uses its own persisted recovery
state rather than weakening or overloading that existing contract.

Add an independent `cloud_agent_incidents` table whose records can outlive Job
deletion. A notice contains only:

- opaque incident ID;
- former Job ID;
- sanitized subject;
- stage and typed reason code;
- Flow and Canva attempt counts;
- human-readable Thai message;
- creation time; and
- dismissed time or unread flag.

It must never contain scripts, prompts, cookies, tokens, signed URLs, browser
profiles, filesystem paths, provider payloads, or raw exception text.

## Meaningful Progress Contract

Lease renewal, Worker heartbeat, SQLite `updated_at`, SSE heartbeats, log lines,
and browser liveness do not count as progress. Only verified workflow milestones
advance `last_progress_at`.

Examples include:

- TTS artifact successfully validated;
- observable Flow inventory changes;
- a Flow failure card becomes conclusively visible;
- survivor naming is verified;
- an archive snapshot downloads and parses successfully;
- a targeted replacement appears and validates;
- Canva cleanup completes;
- audio upload or timeline insertion completes;
- each video upload or timeline insertion completes;
- caption generation is requested and captions become stable;
- export begins, downloads, or validates; and
- a durable checkpoint advances.

Repeated observation of the same card count, the same Canva element, or the
same state does not refresh the timestamp. The one-hour rule measures
continuous inactivity, not total Job runtime. Real new milestones may allow a
long Job to continue beyond one hour.

## Google Flow Detection and Recovery

### Normal success

The original paid-generation fence is persisted before the batch Generate
action. If six completed videos stabilize, the existing semantic rename,
project download, archive materialization, and `FLOW_READY` path continues.

### Fast failed-card detection

The generation observer checks visible output cards even when the completed
video count is below six. A conclusive failure marker such as
`Audio Generation Failed`, another recognized failure label, or an equivalent
accessible failed state ends the normal wait immediately. Processing, loading,
or ambiguous cards continue to wait within existing bounds.

The failed card is evidence that the batch is incomplete, not evidence of its
original clip number. The system must still establish semantic survivor names
and inspect a downloaded archive before selecting a prompt.

### First partial snapshot

1. Submit a bounded Agent instruction to rename each successful survivor to its
   original `clip N` number from the initial request.
2. Explicitly forbid closing gaps, renumbering survivors, duplicate names, or
   naming the failed placeholder.
3. Wait for the Agent response, reload, and inspect visible semantic names.
4. Download the Flow Project into an attempt-specific snapshot path rather than
   overwriting the canonical complete archive.
5. Parse the archive with a partial-inventory validator that accepts exactly
   five unique semantic MP4 names drawn from `clip 1` through `clip 6`.
6. Validate every extracted survivor as a portrait 9:16 Flow source.
7. Compute the one absent index and persist the index plus a digest of survivor
   names and validated media identities.

The missing index must be corroborated by the stable output-slot order or
provider-visible request/card metadata as well as the semantic gap in the
partial archive. A set of five plausible names produced only by Agent chat is
not sufficient because an Agent could renumber survivors incorrectly. If the
UI no longer exposes enough evidence to corroborate the mapping, stop with an
unresolved-mapping incident rather than selecting a prompt by guesswork.

The partial validator is separate from the existing final archive materializer.
The final materializer must continue to reject anything other than a complete,
unique set `clip 1` through `clip 6`.

If the Agent response claims success but the archive is ambiguous, duplicated,
out of range, invalid, or not exactly five semantic videos, recovery stops. The
system never trusts the chat claim over the archive.

### Targeted replacement

For missing index `N`, select the one `job.clip_plan.segments` entry whose index
equals `N`. Use its stored `video_prompt` verbatim inside a fixed wrapper:

```text
Generate exactly one video for CLIP N.
Use the following video prompt verbatim; do not rewrite it.
Name only the new completed video "clip N".
Do not rename, remove, or recreate existing videos.

<exact stored video_prompt>
```

Before clicking Generate, atomically reserve the next attempt and set recovery
state to `SUBMISSION_UNRESOLVED`. A crash after that write may not issue another
paid request blindly. On restart, first inspect current remote inventory and a
fresh archive:

- if `clip N` already exists and validates, continue to final verification;
- if the submission is visibly still running, continue reconciliation;
- if Flow proves the attempt failed without a usable video, resolve that
  attempt and reserve the next one if the two-attempt budget remains; or
- if the remote result cannot be distinguished safely, stop rather than risk a
  duplicate paid submission.

### Fresh final archive

After the replacement appears, download the whole Flow Project again to a new
attempt-specific snapshot. Validate it as exactly six unique semantic videos.
If it passes, atomically materialize that latest archive into canonical local
`clip_01.mp4` through `clip_06.mp4`. The first partial ZIP remains only temporary
diagnostic/recovery evidence and is not mixed into production files.

### Merge fallback

Use a merge only if the later provider download verifiably contains exactly the
new missing semantic clip rather than the whole project:

1. Copy the five previously validated survivors and the one later replacement
   into a new staging directory.
2. Revalidate names, uniqueness, media format, resolution, aspect ratio, and
   the persisted survivor baseline.
3. Require exactly `clip 1` through `clip 6` with no additional MP4 entries.
4. Atomically replace canonical Flow files only after every validation passes.

Never merge into the first extraction directory in place. A partial copy,
validation failure, or process crash leaves canonical artifacts unchanged.

## Flow Failure Rules

Automatic recovery is denied when:

- anything other than exactly one clip is missing;
- the one missing number cannot be proven;
- survivor names are duplicated, out of range, or gap-closing;
- any survivor or replacement media fails validation;
- the Agent changes existing names incorrectly;
- a snapshot has unexpected or ambiguous MP4 entries;
- the paid-submission outcome is unresolved after reconciliation; or
- both targeted replacement attempts are exhausted.

Terminal Flow reason codes include:

- `FLOW_MISSING_CLIP_UNRESOLVED`;
- `FLOW_RECOVERY_SUBMISSION_UNRESOLVED`;
- `FLOW_RECOVERY_EXHAUSTED`; and
- `FLOW_ARCHIVE_VALIDATION_FAILED`.

## Canva Attempt and Restart Contract

Canva starts only from checkpoint `FLOW_READY` with six canonical validated
Flow clips and the existing validated narration artifact. A restart never
changes that checkpoint and never calls Research, TTS, or Flow.

Each Canva attempt uses the persisted Job design URL and performs the approved
audio-first sequence:

1. remove all video and audio timeline media; captions disappear as a
   consequence and are not searched for during cleanup;
2. clean managed uploads as required by the existing Job workspace contract;
3. upload media;
4. add narration first;
5. observe narration start at zero without moving or trimming it;
6. add the six videos;
7. observe the first video start at zero without moving or trimming it;
8. mute source-video audio;
9. generate captions and wait up to 90 seconds for at least one caption track
   whose count and text stabilize for five seconds;
10. export, download, and validate the final file.

No attempt adjusts video speed, trims or extends video, aligns media lengths,
or compares narration duration with combined video duration. An unavailable
start-position observation is a warning; a clearly observed non-zero start is
a real verification failure.

If no new meaningful Canva milestone occurs for 20 minutes, the supervisor
terminates that child before reserving a restart. A replacement child resumes
the same Job at `FLOW_READY`, opens the persisted Canva design, clears it, and
rebuilds from the beginning. The initial attempt plus four reserved restarts
allows five Canva attempts total.

Exhaustion uses `CANVA_RESTART_EXHAUSTED`. Login, CAPTCHA, 2FA, payment,
security confirmation, destructive confirmation, or unknown modal states are
not auto-dismissed and retain the existing human-required safety boundary.

## One-Hour Global Stall Rule

The global deadline applies in every active stage and is based on
`last_progress_at`. Flow's fast failure handling and Canva's 20-minute restart
take precedence when they can act earlier. A successful restart or a newly
verified milestone resets the inactivity clock; merely starting a new child
does not.

The clock is initialized when a Worker claims the Job. A Job legitimately
waiting in `QUEUED` behind another active Job is not timed out by this rule.
Completed, failed, cancelled, paused, and already human-required records are
also outside the active watchdog. If the Worker service itself is offline, the
existing health indicator reports that condition; durable timeout
reconciliation runs once when the Worker returns.

The one-hour inactivity rule is the outer safety limit and may preempt an
unused retry budget. For example, repeated Canva restarts that never reach a
single new verified milestone do not keep the Job alive merely because fewer
than four restarts have been consumed. When an attempt reaches a real new
milestone, the global inactivity clock advances normally.

At one hour without meaningful progress:

1. stop and confirm termination of the active Job child and browser process
   group;
2. make the Job unclaimable while cleanup runs;
3. persist a pending sanitized incident with `JOB_STALLED_TIMEOUT` and the
   stalled stage;
4. stage the Job directory through the existing path-safe storage boundary;
5. purge the staged local directory;
6. in one SQLite transaction, finalize the incident and delete the Job record;
7. emit a safe incident event; and
8. never requeue or create a new Job.

Retry-budget exhaustion follows the same stop, local-delete, durable-incident,
and no-requeue terminal path, using the stage-specific reason code.

If child termination cannot be confirmed, do not delete anything. If local
staging or purge fails, retain an unclaimable human-required Job and publish
`JOB_DELETE_CLEANUP_FAILED`; do not report that deletion succeeded. If the
final SQLite transaction fails after local cleanup, retain the unclaimable Job
as a durable cleanup tombstone and surface the same typed failure for manual
resolution. Remote Flow and Canva assets remain untouched in all cases.

## Incident Events and WebUI

Extend the existing safe event transport with `job.incident`. It is an
invalidation signal containing only incident ID, former Job ID, reason code,
stage, and creation time. Event delivery remains non-blocking and can never
participate in production success, recovery, cleanup, or deletion.

The Cloud Agent page:

- reads unread incidents once on initial render and SSE reconnect;
- responds to `job.incident` with one incident reconciliation read;
- clears the selected Job ID if that Job has been deleted;
- shows a Thai banner with the former Job ID, subject, stalled stage, attempts,
  time, and a Dismiss action; and
- never polls every two seconds.

Example messages:

- `งานถูกยกเลิกอัตโนมัติ: Google Flow สร้าง clip 2 ไม่สำเร็จหลังลองเพิ่มอีก 2 รอบ ระบบไม่ได้เริ่มงานใหม่`
- `งานถูกลบอัตโนมัติ: ไม่มีความคืบหน้าในขั้น Canva เกิน 1 ชั่วโมง ระบบไม่ได้เริ่มงานใหม่`

Missing SSE delivery is harmless: the incident remains durable and appears on
the next page load or reconnect.

## Error Handling and Isolation

- Provider detection errors are contained within their Job child.
- A Flow recovery error never deletes or rewrites validated canonical files
  until a complete replacement set is staged and validated.
- A Canva restart never mutates Flow recovery counters or source artifacts.
- Incident or notification rendering failure never affects another Job or the
  completed-video library.
- Supervisor failure does not authorize another paid submission. Durable
  unresolved fences force reconciliation after restart.
- One Job's forced termination targets only its process group; it must not
  restart the whole Worker service or interrupt another Job.

## Configuration

Add conservative configuration values with validated bounds:

- Flow targeted replacement retries: `2`;
- Canva additional restarts: `4`;
- Canva no-progress deadline: `20 minutes`;
- global no-progress deadline: `60 minutes`;
- graceful child termination period; and
- bounded progress-signal queue capacity.

These are operational guardrails, not WebUI settings. The product behavior
must not silently change through a Custom Prompt.

## Verification

Automated tests use fake pages, archives, clocks, processes, and event sinks;
they must not contact paid providers.

Required coverage:

1. A visible failed Flow card ends the normal wait without waiting 30 minutes.
2. A five-video partial archive with unique semantic names produces exactly
   one missing index.
3. Duplicate, ambiguous, gap-closing, unsafe, or multi-missing inventories are
   rejected without a paid replacement submission.
4. The exact matching segment `video_prompt` is wrapped without mutation.
5. A targeted attempt is persisted before Generate; child crash and restart
   reconcile remote inventory without blind resubmission.
6. At most two targeted replacement submissions occur.
7. A fresh complete second archive is preferred and atomically materialized.
8. The merge fallback accepts five validated survivors plus only the missing
   validated replacement and rejects every other combination.
9. Canva no-progress at 20 minutes terminates the old child before restart.
10. Canva performs at most four additional attempts and never repeats TTS or
    Flow.
11. Heartbeats, leases, `updated_at`, and repeated identical observations do
    not reset the meaningful-progress deadline.
12. New verified milestones do reset that deadline.
13. One-hour inactivity terminates the child before local cleanup and Job
    deletion.
14. Failed termination or cleanup preserves an unclaimable safe record and a
    typed incident rather than claiming successful deletion.
15. A deleted Job's incident survives, reaches the WebUI through SSE, and also
    appears after reconnect without two-second polling.
16. Existing queue claiming, lease renewal, paid-generation fence, controls,
    audio-first Canva behavior, caption verification, final validation,
    completed-video library, deletion, and event-isolation regressions pass.

After automated gates, deployment verification uses controlled test Jobs:

- one six-clip normal success proving no recovery behavior changes the happy
  path;
- one fake-provider or safely staged single-missing recovery proving the exact
  prompt and fresh archive flow;
- one forced Canva inactivity test proving process termination and restart
  isolation; and
- one incident delivery/reconnect test proving production remains independent
  of WebUI availability.

No destructive stall test may delete a real completed Job, remote Flow project,
or Canva design.
