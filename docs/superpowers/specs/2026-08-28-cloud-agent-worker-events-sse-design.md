# Cloud Agent Worker Events and SSE Design

## Goal

Replace the Cloud Agent WebUI's two-second active-job polling with event-driven
updates from the Worker. Production progress must continue to update at real
workflow transitions, and a newly completed video must appear automatically in
**วีดีโอที่สร้าง**. Notification, SSE, WebUI, or media-rendering failures must
never change, delay materially, or fail the production workflow.

## Accepted Product Decisions

- The Worker remains the authority for job progress and completion.
- The WebUI does not poll an active job every two seconds.
- Progress updates are emitted when the durable job status, checkpoint,
  current step, or progress changes.
- A distinct `job.completed` event is emitted after the durable transition to
  `COMPLETED`; this event refreshes both Production status and the video
  library.
- The existing Worker queue polling remains unchanged. It checks for new work
  only while the Worker is idle and is not WebUI status polling.
- SSE is the one-way server-to-browser transport. A passive SSE connection is
  not a repeating job query and does not call providers or consume credits.
- Page load and SSE reconnect perform one reconciliation read so that durable
  DB/filesystem state remains authoritative if a transient event is missed.

## Architecture

The event path is deliberately downstream of the durable workflow state:

```text
Workflow validates artifact
        |
        v
SQLite job update commits
        |
        v
Worker event adapter enqueues notification (non-blocking)
        |
        v
Background dispatcher -> localhost FastAPI event intake
        |
        v
In-memory SSE hub -> exact Nginx SSE route -> browser component
        |
        v
One WebUI reconciliation read -> progress and/or video library rerender
```

The production path ends logically at the committed SQLite update. Every box
after it is optional delivery infrastructure. No browser connection, API
response, queue capacity, or event-delivery result participates in deciding
whether a Job succeeds.

## Durable State and Event Semantics

SQLite remains the source of truth. Events are invalidation signals telling a
browser to read current state; they are not a second job database.

The job-store completion invariant is:

- transitioning to `status=COMPLETED` also writes
  `checkpoint=COMPLETED`, `progress=100`, and a non-empty UTC
  `completed_at`;
- the canonical validated final-video path is already committed; and
- the video library still independently verifies the DB gates and physical
  final file before showing a card.

The current workflow writes `COMPLETED` but does not populate `completed_at`.
This design corrects that invariant at the durable store boundary so all
completion callers receive the same behavior and newest-first ordering is
reliable.

Two public event types are sufficient:

- `job.updated`: emitted only when `status`, `checkpoint`, `current_step`, or
  `progress` changes;
- `job.completed`: emitted for the durable transition to `COMPLETED`.

An event contains only:

```json
{
  "event_id": "opaque-id",
  "type": "job.updated",
  "job_id": "uuid",
  "status": "FLOW_READY",
  "checkpoint": "FLOW_READY",
  "current_step": "flow_ready",
  "progress": 60,
  "updated_at": "2026-08-28T00:00:00.000000+00:00",
  "completed_at": ""
}
```

It never contains scripts, prompts, local paths, browser data, provider data,
keys, cookies, signed URLs, or raw error details. `job.completed` uses the same
shape with the final state and non-empty `completed_at`.

## Worker Isolation

The workflow continues to use the existing job-store interface. In Worker
composition only, an event-publishing store adapter observes successful
`patch_job` results and submits relevant state changes with `publish_nowait`.
This avoids inserting network calls throughout `workflow.py` and keeps the
workflow independently testable.

`publish_nowait` writes to a small bounded in-process notification queue. A
dedicated daemon dispatcher performs the localhost HTTP delivery to FastAPI
with a short timeout. Its guarantees are:

- enqueue occurs only after the DB update succeeds;
- a full queue drops/coalesces notification signals rather than blocking the
  Worker;
- HTTP failure, timeout, invalid response, dispatcher failure, or shutdown
  event loss is logged safely and never raised into Worker execution;
- notification delivery is not retried inside the production workflow; and
- lease renewal, job claiming, controls, Flow, Canva, TTS, validation, and
  completion semantics remain unchanged.

Coalescing may retain only the newest pending update for one Job. This is safe
because the browser reads the latest durable snapshot after any signal; it
does not replay workflow history from events.

## API Event Hub and Routes

FastAPI owns a process-local event hub with bounded subscriber queues.

### Internal intake

`POST /api/v1/cloud-agent/internal/events`

- Called only from the local Worker through `127.0.0.1:8080`.
- Validates the narrow event model and broadcasts with a non-blocking enqueue.
- Is not exposed by Nginx.
- Returns immediately after acceptance; it never queries providers or media.

### Browser stream

`GET /api/v1/cloud-agent/events/stream`

- Exposed through an exact-match Nginx location protected by the same Basic
  Authentication as the Streamlit application.
- Uses `text/event-stream`, disables proxy buffering, and keeps a long read
  timeout.
- Sends a `sync_required` event immediately on connection/reconnection.
- Sends periodic SSE comment heartbeats only to keep proxies from closing an
  idle connection. Heartbeats do not query SQLite and do not rerender the UI.
- Uses bounded per-subscriber queues. A slow subscriber is marked for
  reconciliation or disconnected; it can never apply backpressure to the
  Worker or API intake.

No general `/api/` proxy is opened publicly. Only the exact SSE GET path is
proxied to FastAPI; the internal intake and existing internal API routes remain
reachable only on the host.

Persistent event history, Redis, RabbitMQ, and guaranteed event replay are not
required. `sync_required` plus durable-state reconciliation provides recovery
without turning delivery infrastructure into part of production correctness.

## WebUI Behavior

A small isolated Streamlit browser component opens an `EventSource` to the
same-origin SSE path. It passes only the latest event ID/type/job ID back to
Streamlit and reconnects using normal EventSource behavior.

The current `st.fragment(run_every=2)` active-job refresh and
`LIVE_JOB_REFRESH_SECONDS` are removed. The WebUI performs reads only in these
cases:

1. initial page render;
2. a relevant `job.updated` event for the selected active Job;
3. any `job.completed` event, because the shared library may have a new card;
4. `sync_required` after SSE connection/reconnection;
5. existing explicit user actions such as selecting a Job, retrying, deleting,
   paginating, or manually refreshing the page.

For `job.updated`, the WebUI fetches that Job once and rerenders Production
status. For `job.completed`, it fetches the Job once and reloads the current
video-library page once. Event IDs are remembered in session state so a
Streamlit rerun does not process the same notification repeatedly.

If SSE is unavailable, the last confirmed status stays visible with a small
connection indicator. Production continues normally. Opening or refreshing
the page reconciles the correct status and library from the API.

## Video-Library Failure Boundary

The video library remains a read-only downstream consumer of completed Job
state. It cannot call or control the Worker.

- List failure renders a library-level unavailable message, then the page
  continues to Job controls.
- Each card owns its media-load boundary. Timeout, connection failure, 404,
  empty payload, or invalid media URL renders the placeholder
  **วิดีโอนี้โหลดไม่ได้ กรุณาลองใหม่ภายหลัง** for that card only.
- An unexpected library renderer failure is logged and replaced by a
  library-level placeholder. Production status, controls, and other page
  sections remain usable.
- No load error deletes a Job, retries production, contacts Flow/Canva, or
  changes durable status.

## Configuration and Deployment

- Add only notification queue size, local intake URL, delivery timeout, and SSE
  heartbeat settings with conservative defaults.
- Keep `cloud_agent_worker_poll_seconds` unchanged.
- Update the version-controlled Nginx example with an exact SSE location;
  deployment applies the equivalent live Nginx configuration and validates it
  before reload.
- API, Worker, and WebUI services continue as separate existing processes. No
  additional daemon, provider, browser, Redis, or message broker is introduced.

## Observability

Safe logs distinguish:

- event enqueued, coalesced, or dropped;
- localhost delivery accepted or failed;
- SSE subscriber connected/disconnected;
- WebUI stream unavailable; and
- reconciliation failure.

Logs may include event type and Job ID but never the excluded sensitive fields.
Notification failures are warnings, not production Job errors.

## Verification

Automated tests use fake publishers/transports only and must not contact TTS,
Google Flow, Canva, browsers, or paid providers.

Coverage includes:

1. A successful state patch emits only after the DB commit and contains the
   safe public projection.
2. Non-progress metadata patches do not emit UI events.
3. Transition to `COMPLETED` sets `completed_at` once and emits
   `job.completed`.
4. Queue full, dispatcher exception, timeout, and API-unavailable cases cannot
   change a successful workflow result or Job status.
5. The API rejects malformed event intake and never exposes the internal POST
   through the documented public proxy route.
6. SSE connection/reconnection produces `sync_required`; slow subscribers do
   not block publishers.
7. The WebUI contains no two-second active-job polling and performs one Job
   read per relevant event.
8. Intermediate events continue to update the five-stage Progress UI.
9. Completion refreshes the video library exactly once and newest-first order
   uses the populated `completed_at`.
10. One failed video-card load renders its placeholder while later cards and
    Job controls still render.
11. Existing workflow, Worker lease/queue, controls, video library,
    pagination, deletion, and final-media tests remain passing.

Deployment verification includes Nginx configuration validation, one queued
test Job observed through intermediate event-driven progress, automatic
library insertion at completion, SSE reconnect reconciliation, and deliberate
API/SSE interruption proving the Worker still completes successfully.

## Non-goals

- No change to the Worker polling interval used to discover queued Jobs.
- No provider call, credit use, Flow action, Canva action, TTS action, or
  browser automation caused by event delivery.
- No event-sourced Job history or replacement for SQLite as source of truth.
- No public exposure of the general FastAPI surface or internal event intake.
- No guaranteed delivery requirement: missed signals recover through
  reconciliation.
- No filesystem watcher and no inference of completion from MP4 existence
  alone.
