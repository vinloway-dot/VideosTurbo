# Cloud Agent Worker Events and SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace two-second Cloud Agent WebUI job polling with Worker-originated event updates while keeping progress live, inserting completed videos automatically, and isolating every notification or media-rendering failure from production.

**Architecture:** Durable SQLite state remains authoritative. A Worker-only job-store adapter emits safe state projections into a bounded non-blocking dispatcher after successful commits; the dispatcher posts to a localhost FastAPI event hub, which exposes one exact SSE stream to an inline Streamlit Components v2 listener through Nginx. Browser events trigger one reconciliation read, while page load/reconnect and physical-file validation recover from missed notifications.

**Tech Stack:** Python 3.11+, SQLite, Pydantic, requests, FastAPI/Starlette `StreamingResponse`, asyncio, Streamlit 1.59.1 Components v2, Nginx SSE proxying, pytest 9.1.1, Ruff 0.15.21.

**Spec:** `docs/superpowers/specs/2026-08-28-cloud-agent-worker-events-sse-design.md`

## Global Constraints

- Do not change the Script → Voice → Flow → Canva → Export workflow decisions or provider behavior.
- The SQLite update is the production success boundary. Event enqueue, delivery, API intake, SSE, browser, and media failures must never change a successful Job result.
- Remove only the WebUI's `st.fragment(run_every=2)` polling. Keep `cloud_agent_worker_poll_seconds=2` for idle queue discovery.
- Emit UI events only after durable changes to `status`, `checkpoint`, `current_step`, or `progress`.
- Emit `job.completed` only for the durable transition to `COMPLETED`; populate `completed_at` exactly once.
- Event payloads contain only event ID/type, Job ID, status, checkpoint, current step, progress, updated time, and completion time.
- Never include scripts, prompts, errors, local paths, provider data, keys, cookies, signed URLs, browser data, or media bytes in events or logs.
- Use a bounded non-blocking Worker queue. Full queue, timeout, HTTP error, malformed response, thread failure, or API outage logs a safe warning and cannot escape into workflow execution.
- Use FastAPI/Starlette already installed; do not add Redis, RabbitMQ, SSE packages, providers, daemons, or paid calls.
- Use inline Streamlit Components v2 (`st.components.v2.component`); do not introduce legacy Components v1 APIs, Node, npm, or a packaged component.
- Nginx exposes only the exact SSE GET path. The internal event POST and general FastAPI surface remain loopback-only.
- SSE keepalive comments do not query SQLite and do not rerender Streamlit.
- Page load and SSE reconnect each perform one durable-state reconciliation; missed events do not require persistent event history.
- A failed video card displays **วิดีโอนี้โหลดไม่ได้ กรุณาลองใหม่ภายหลัง** and cannot prevent later cards, Production status, or Job controls from rendering.
- Automated tests must not contact TTS, Google Flow, Canva, browser profiles, external networks, or paid providers.
- Every implementation Task follows RED → smallest GREEN → focused tests → related regression → Ruff → commit → two-stage review.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `app/services/cloud_agent/job_store.py` | Enforce `COMPLETED`/`completed_at` invariant and compatibility backfill. |
| `app/services/cloud_agent/job_events.py` | Safe event model, sink protocol, Job projection, and Worker-only event-publishing store adapter. |
| `app/services/cloud_agent/event_dispatcher.py` | Bounded non-blocking queue, daemon delivery thread, and localhost HTTP transport. |
| `app/services/cloud_agent/event_hub.py` | Bounded async SSE subscriber hub, sync events, heartbeat formatting, and slow-client recovery. |
| `app/services/cloud_agent/factory.py` | Compose the event-enabled store and dispatcher only in the Worker process. |
| `app/config/config.py` | Conservative event queue, intake URL, timeout, and heartbeat defaults. |
| `app/controllers/v1/cloud_agent.py` | Internal event intake and browser SSE endpoints using the existing router. |
| `webui/cloud_agent_events.py` | Inline Streamlit Components v2 EventSource listener and pure event-action classification. |
| `webui/cloud_agent.py` | Replace timed fragment polling with event-driven fragment reconciliation. |
| `webui/cloud_agent_ui.py` | Per-card media exception boundary and Thai placeholder. |
| `deploy/nginx/videosturbo.conf.example` | Exact authenticated SSE proxy; no general API proxy. |
| `deploy/cloud-agent/README.md` | SSE deployment and validation instructions. |
| `test/services/cloud_agent/test_job_store.py` | Completion timestamp and legacy-row backfill tests. |
| `test/services/cloud_agent/test_job_events.py` | Projection, filtering, post-commit emission, duplicate completion, and sink-isolation tests. |
| `test/services/cloud_agent/test_event_dispatcher.py` | Queue-full, HTTP failure, timeout, and shutdown tests with fake transports. |
| `test/services/cloud_agent/test_event_hub.py` | Sync, broadcast, heartbeat, bounded subscriber, and cleanup tests. |
| `test/services/test_cloud_agent_controller.py` | Event route contracts, payload redaction, intake, and SSE response tests. |
| `test/services/test_cloud_agent_deploy.py` | Exact Nginx route and buffering/security assertions. |
| `test/services/test_cloud_agent_events.py` | Components v2 source contract and event-action classification tests. |
| `test/services/test_cloud_agent_webui.py` | No-timer polling, one-read reconciliation, completion rerun, and library isolation tests. |
| `test/services/test_cloud_agent_ui.py` | Individual media-card placeholder and continuation tests. |

---

### Task 1: Make Completion Time a Durable Store Invariant

**Files:**
- Modify: `app/services/cloud_agent/job_store.py:100-160,329-357`
- Modify: `test/services/cloud_agent/test_job_store.py`
- Modify: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**
- Consumes: existing `CloudJobStore.patch_job(job_id: str, **changes) -> CloudJobRecord`.
- Produces: the same signature, with a non-empty `completed_at` added exactly once when status first becomes `CloudJobStatus.COMPLETED`; legacy completed rows with empty completion time are backfilled from their durable `updated_at`.

- [ ] **Step 1: Write failing completion and compatibility tests**

Add focused tests equivalent to:

```python
def test_first_completed_transition_sets_completion_time_once(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())

    completed = store.patch_job(job.id, status=CloudJobStatus.COMPLETED)
    retained = store.patch_job(job.id, current_step="completed")

    assert completed.completed_at
    assert completed.checkpoint is CloudJobCheckpoint.COMPLETED
    assert completed.progress == 100
    assert retained.completed_at == completed.completed_at


def test_store_backfills_legacy_completed_row_from_updated_at(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    store = CloudJobStore(str(db_path))
    job = store.create_job(_request())
    completed = store.patch_job(job.id, status=CloudJobStatus.COMPLETED)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE cloud_agent_jobs SET completed_at = '' WHERE id = ?",
            (job.id,),
        )

    reopened = CloudJobStore(str(db_path))

    restored = reopened.get_job(job.id)
    assert restored is not None
    assert restored.completed_at == completed.updated_at
```

Extend an existing successful workflow test with:

```python
assert result.status is CloudJobStatus.COMPLETED
assert result.completed_at
```

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_job_store.py::test_first_completed_transition_sets_completion_time_once \
  test/services/cloud_agent/test_job_store.py::test_store_backfills_legacy_completed_row_from_updated_at \
  test/services/cloud_agent/test_workflow.py -q
```

Expected: the new completion assertion fails because the current workflow/store leaves `completed_at` empty; the backfill assertion fails before compatibility SQL exists.

- [ ] **Step 3: Implement the invariant and one-time-compatible backfill**

In `_initialize`, after schema compatibility is established, execute the narrow idempotent repair:

```python
connection.execute(
    """UPDATE cloud_agent_jobs
       SET completed_at = updated_at
       WHERE status = ? AND completed_at = ''""",
    (CloudJobStatus.COMPLETED.value,),
)
```

In `patch_job`, compute one `now`, enforce the completed checkpoint/progress,
preserve an existing completion timestamp, and populate it only for a
first/legacy transition to `COMPLETED`:

```python
now = _utc_now()
next_status = changes.get("status", existing.status)
if next_status is CloudJobStatus.COMPLETED:
    changes["checkpoint"] = CloudJobCheckpoint.COMPLETED
    changes["progress"] = 100
    if not str(changes.get("completed_at") or existing.completed_at).strip():
        changes["completed_at"] = now
candidate_data.update(changes)
candidate_data["updated_at"] = now
```

Do not update `completed_at` for `FAILED`, `CANCELLED`, metadata patches, lease release, or repeated reads.

- [ ] **Step 4: Run focused and library regressions**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_workflow.py \
  test/services/test_cloud_agent_video_library.py -q
uv run ruff check app/services/cloud_agent/job_store.py \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_workflow.py
```

Expected: all tests and Ruff pass; newest-first library ordering has a durable timestamp.

- [ ] **Step 5: Commit Task 1**

```bash
git add app/services/cloud_agent/job_store.py \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_workflow.py
git commit -m "fix: persist cloud job completion time"
```

---

### Task 2: Emit Safe Events After Worker Store Commits

**Files:**
- Create: `app/services/cloud_agent/job_events.py`
- Create: `test/services/cloud_agent/test_job_events.py`

**Interfaces:**
- Consumes: `CloudJobRecord`, `CloudJobStore`, and a sink implementing `publish_nowait(event: CloudJobEvent) -> bool`.
- Produces:
  - `CloudJobEventType(str, Enum)` with `JOB_UPDATED="job.updated"` and `JOB_COMPLETED="job.completed"`.
  - `CloudJobEvent(BaseModel)` with the safe event fields from the Spec.
  - `JobEventSink(Protocol).publish_nowait(event: CloudJobEvent) -> bool`.
  - `EventPublishingCloudJobStore(CloudJobStore)` overriding `patch_job` while retaining all existing store behavior.

- [ ] **Step 1: Write failing safe-projection and emission tests**

Create tests equivalent to:

```python
class RecordingSink:
    def __init__(self):
        self.events = []

    def publish_nowait(self, event):
        self.events.append(event)
        return True


def test_status_patch_emits_safe_projection_after_commit(tmp_path):
    sink = RecordingSink()
    store = EventPublishingCloudJobStore(
        str(tmp_path / "agent.sqlite3"), sink=sink
    )
    job = store.create_job(_request())

    changed = store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_GENERATING,
        current_step="tts_generating",
        progress=15,
        error_message="must-not-leak",
    )

    assert store.get_job(job.id) == changed
    assert sink.events[0].model_dump(mode="json") == {
        "event_id": sink.events[0].event_id,
        "type": "job.updated",
        "job_id": job.id,
        "status": "TTS_GENERATING",
        "checkpoint": "NONE",
        "current_step": "tts_generating",
        "progress": 15,
        "updated_at": changed.updated_at,
        "completed_at": "",
    }
    assert "must-not-leak" not in sink.events[0].model_dump_json()


def test_non_progress_and_duplicate_patches_do_not_emit(tmp_path):
    sink = RecordingSink()
    store = EventPublishingCloudJobStore(str(tmp_path / "agent.sqlite3"), sink=sink)
    job = store.create_job(_request())

    store.patch_job(job.id, voice_file="voice.mp3")
    store.patch_job(job.id, progress=0)

    assert sink.events == []


def test_completed_transition_emits_one_completed_event(tmp_path):
    sink = RecordingSink()
    store = EventPublishingCloudJobStore(str(tmp_path / "agent.sqlite3"), sink=sink)
    job = store.create_job(_request())

    completed = store.patch_job(
        job.id,
        status=CloudJobStatus.COMPLETED,
        current_step="completed",
        progress=100,
    )
    store.patch_job(job.id, current_step=completed.current_step)

    assert [event.type.value for event in sink.events] == ["job.completed"]
    assert sink.events[0].completed_at == completed.completed_at
```

Add a sink that raises from `publish_nowait` and assert the committed Job still returns successfully and remains readable.

- [ ] **Step 2: Run the new file and observe RED**

Run:

```bash
uv run pytest test/services/cloud_agent/test_job_events.py -q
```

Expected: collection fails because `job_events.py` and its interfaces do not exist.

- [ ] **Step 3: Implement the event model and Worker-only store adapter**

Use UUID event IDs and a public-state projection:

```python
from pydantic import BaseModel, ConfigDict, Field


class CloudJobEventType(str, Enum):
    JOB_UPDATED = "job.updated"
    JOB_COMPLETED = "job.completed"


class CloudJobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    type: CloudJobEventType
    job_id: str = Field(min_length=1, max_length=64)
    status: CloudJobStatus
    checkpoint: CloudJobCheckpoint
    current_step: str = Field(max_length=128)
    progress: int = Field(ge=0, le=100)
    updated_at: str = Field(min_length=1, max_length=64)
    completed_at: str = Field(max_length=64)
```

`EventPublishingCloudJobStore.patch_job` must:

1. read the prior record;
2. call `super().patch_job` and allow DB errors to propagate normally;
3. compare only `(status, checkpoint, current_step, progress)`;
4. return without publishing when that projection is unchanged;
5. create `job.completed` only when prior status was not completed and new status is completed, otherwise create `job.updated`; and
6. catch/log any sink exception without changing the returned record.

The adapter must not override claiming, leases, controls, list/delete, or schema behavior.

- [ ] **Step 4: Run focused tests, store regressions, and Ruff**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_job_events.py \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_worker.py -q
uv run ruff check app/services/cloud_agent/job_events.py \
  test/services/cloud_agent/test_job_events.py
```

Expected: all tests and Ruff pass; a throwing sink cannot fail a store update.

- [ ] **Step 5: Commit Task 2**

```bash
git add app/services/cloud_agent/job_events.py \
  test/services/cloud_agent/test_job_events.py
git commit -m "feat: emit safe cloud job state events"
```

---

### Task 3: Deliver Worker Events Without Blocking Production

**Files:**
- Create: `app/services/cloud_agent/event_dispatcher.py`
- Create: `test/services/cloud_agent/test_event_dispatcher.py`
- Modify: `app/config/config.py:28-54`
- Modify: `app/services/cloud_agent/factory.py:34-77,148-158`
- Modify: `test/services/test_config.py:520-550`
- Modify: `test/services/cloud_agent/test_worker.py`

**Interfaces:**
- Consumes: `CloudJobEvent` and `JobEventSink` from Task 2.
- Produces:
  - `RequestsJobEventTransport(url: str, timeout_seconds: float).send(event) -> None`.
  - `CloudJobEventDispatcher(transport, queue_size: int)` implementing `publish_nowait(event) -> bool` and `close(timeout_seconds: float = 1.0) -> None`.
  - `build_workflow(*, store: CloudJobStore | None = None) -> CloudAgentWorkflow`.
  - Worker configuration keys with defaults: queue `128`, intake URL `http://127.0.0.1:8080/api/v1/cloud-agent/internal/events`, delivery timeout `0.5`, SSE heartbeat `25` seconds.

- [ ] **Step 1: Write failing dispatcher isolation tests**

Create deterministic tests using injected transports and synchronization events:

```python
def test_publish_nowait_returns_immediately_and_dispatches_in_background():
    delivered = threading.Event()
    transport = lambda event: delivered.set()
    dispatcher = CloudJobEventDispatcher(transport=transport, queue_size=2)
    try:
        assert dispatcher.publish_nowait(_event()) is True
        assert delivered.wait(timeout=1.0)
    finally:
        dispatcher.close()


def test_full_queue_drops_signal_without_raising():
    release = threading.Event()
    started = threading.Event()

    def blocked(_event):
        started.set()
        release.wait(timeout=1.0)

    dispatcher = CloudJobEventDispatcher(transport=blocked, queue_size=1)
    try:
        assert dispatcher.publish_nowait(_event("one")) is True
        assert started.wait(timeout=1.0)
        assert dispatcher.publish_nowait(_event("two")) is True
        assert dispatcher.publish_nowait(_event("three")) is False
    finally:
        release.set()
        dispatcher.close()


def test_transport_exception_does_not_kill_dispatcher_or_raise_to_publisher():
    calls = []

    def failing(event):
        calls.append(event.job_id)
        raise requests.Timeout("offline")

    dispatcher = CloudJobEventDispatcher(transport=failing, queue_size=2)
    try:
        assert dispatcher.publish_nowait(_event("job-1")) is True
        assert _wait_until(lambda: calls == ["job-1"])
    finally:
        dispatcher.close()
```

Add an explicit HTTP transport contract test:

```python
def test_requests_transport_posts_only_safe_json_with_timeout(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs)) or Response(),
    )
    event = _event("job-1")

    RequestsJobEventTransport(
        "http://127.0.0.1:8080/api/v1/cloud-agent/internal/events",
        timeout_seconds=0.5,
    ).send(event)

    assert calls == [
        (
            "http://127.0.0.1:8080/api/v1/cloud-agent/internal/events",
            {"json": event.model_dump(mode="json"), "timeout": 0.5},
        )
    ]
    assert "script" not in calls[0][1]["json"]
    assert "final_video" not in calls[0][1]["json"]
```

Add a factory-composition test using a temporary DB path and fake Worker
constructor:

```python
def test_worker_factory_uses_event_store_but_controller_store_does_not(
    monkeypatch, tmp_path
):
    class Sink:
        def publish_nowait(self, _event):
            return True

    monkeypatch.setitem(config.app, "cloud_agent_db_path", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setattr(factory, "CloudJobEventDispatcher", lambda **_kw: Sink())
    monkeypatch.setattr(
        factory,
        "build_workflow",
        lambda *, store: SimpleNamespace(store=store),
    )
    monkeypatch.setattr(factory, "CloudAgentWorker", lambda store, workflow, **kw: store)

    worker_store = factory.build_worker()
    controller_store = cloud_agent_controller.get_cloud_job_store()

    assert isinstance(worker_store, EventPublishingCloudJobStore)
    assert type(controller_store) is CloudJobStore
```

- [ ] **Step 2: Run dispatcher/config/factory tests and observe RED**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_event_dispatcher.py \
  test/services/cloud_agent/test_worker.py \
  test/services/test_config.py::TestConfigPersistence::test_cloud_agent_missing_settings_receive_documented_defaults -q
```

Expected: imports/default assertions fail because the dispatcher and new configuration do not exist.

- [ ] **Step 3: Implement the bounded dispatcher and HTTP transport**

Use `queue.Queue(maxsize=queue_size)` and one daemon thread. `publish_nowait`
calls `put_nowait` and returns `False` on `queue.Full`; it never performs HTTP.
The delivery loop catches `Exception`, logs only event type/Job ID/exception
class, calls `task_done`, and continues. `close` enqueues a private sentinel and
joins for the bounded timeout. If the queue is full during `close`, discard one
stale notification with `get_nowait`/`task_done` before enqueueing the sentinel;
shutdown itself must not block production indefinitely.

The transport uses:

```python
response = requests.post(
    self.url,
    json=event.model_dump(mode="json"),
    timeout=self.timeout_seconds,
)
response.raise_for_status()
```

Before sending, validate that the configured URL uses plain loopback HTTP,
contains no credentials/query/fragment, and has exactly the internal intake
path. Add this test to prevent accidental event disclosure:

```python
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/api/v1/cloud-agent/internal/events",
        "http://user:pass@127.0.0.1:8080/api/v1/cloud-agent/internal/events",
        "http://127.0.0.1:8080/api/v1/cloud-agent/internal/events?copy=1",
    ],
)
def test_transport_rejects_non_loopback_or_credentialed_intake_url(url):
    with pytest.raises(ValueError, match="loopback Cloud Agent event intake"):
        RequestsJobEventTransport(url, timeout_seconds=0.5)
```

Do not log the request body or response payload.

- [ ] **Step 4: Wire the adapter only into Worker composition**

Add defaults to `CLOUD_AGENT_DEFAULTS`, let `build_workflow` accept an optional
store, and construct:

```python
transport = RequestsJobEventTransport(
    url=str(app_config["cloud_agent_event_intake_url"]),
    timeout_seconds=float(app_config["cloud_agent_event_delivery_timeout_seconds"]),
)
dispatcher = CloudJobEventDispatcher(
    transport=transport.send,
    queue_size=int(app_config["cloud_agent_event_queue_size"]),
)
store = EventPublishingCloudJobStore(
    str(app_config["cloud_agent_db_path"]), sink=dispatcher
)
workflow = build_workflow(store=store)
```

Pass `workflow.store` to the existing Worker. Do not change Worker lease renewal,
queue polling, `run_once`, or workflow exception behavior.

- [ ] **Step 5: Run focused and Worker regressions**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_event_dispatcher.py \
  test/services/cloud_agent/test_job_events.py \
  test/services/cloud_agent/test_worker.py \
  test/services/test_config.py -q
uv run ruff check \
  app/config/config.py \
  app/services/cloud_agent/event_dispatcher.py \
  app/services/cloud_agent/factory.py \
  test/services/cloud_agent/test_event_dispatcher.py \
  test/services/cloud_agent/test_worker.py
```

Expected: all tests and Ruff pass. Explicitly verify a failing transport leaves a completed Worker Job as `COMPLETED`.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/config/config.py \
  app/services/cloud_agent/event_dispatcher.py \
  app/services/cloud_agent/factory.py \
  test/services/test_config.py \
  test/services/cloud_agent/test_event_dispatcher.py \
  test/services/cloud_agent/test_worker.py
git commit -m "feat: dispatch cloud job events asynchronously"
```

---

### Task 4: Add the Local Intake and Bounded SSE Hub

**Files:**
- Create: `app/services/cloud_agent/event_hub.py`
- Create: `test/services/cloud_agent/test_event_hub.py`
- Modify: `app/controllers/v1/cloud_agent.py:13-60,140-190,846-920`
- Modify: `test/services/test_cloud_agent_controller.py`

**Interfaces:**
- Consumes: `CloudJobEvent` and `config.app["cloud_agent_sse_heartbeat_seconds"]`.
- Produces:
  - `CloudJobEventHub(subscriber_queue_size: int = 16)`.
  - `await hub.publish(event: CloudJobEvent) -> None`.
  - `hub.stream(*, heartbeat_seconds: float) -> AsyncIterator[str]` yielding valid SSE frames.
  - `POST /api/v1/cloud-agent/internal/events` with HTTP 202.
  - `GET /api/v1/cloud-agent/events/stream` with `text/event-stream`.

- [ ] **Step 1: Write failing hub behavior tests**

Use `asyncio.run` so no new pytest plugin is required:

```python
def test_stream_starts_with_sync_then_receives_published_event():
    async def scenario():
        hub = CloudJobEventHub(subscriber_queue_size=2)
        stream = hub.stream(heartbeat_seconds=1.0)
        first = await anext(stream)
        assert "event: sync_required" in first

        event = _event()
        await hub.publish(event)
        second = await anext(stream)
        assert f"id: {event.event_id}" in second
        assert "event: job.updated" in second
        assert '"job_id":"job-1"' in second
        await stream.aclose()

    asyncio.run(scenario())


def test_idle_stream_yields_comment_heartbeat_without_database_work():
    async def scenario():
        hub = CloudJobEventHub()
        stream = hub.stream(heartbeat_seconds=0.01)
        await anext(stream)  # sync_required
        assert await anext(stream) == ": keep-alive\n\n"
        await stream.aclose()

    asyncio.run(scenario())
```

Add a bounded slow-subscriber test:

```python
def test_slow_subscriber_is_reconciled_without_blocking_publisher():
    async def scenario():
        hub = CloudJobEventHub(subscriber_queue_size=1)
        stream = hub.stream(heartbeat_seconds=1.0)
        await anext(stream)  # initial sync_required

        await hub.publish(_event("job-1", progress=15))
        await hub.publish(_event("job-1", progress=30))

        frame = await anext(stream)
        assert "event: sync_required" in frame
        await stream.aclose()
        assert hub.subscriber_count == 0

    asyncio.run(scenario())
```

- [ ] **Step 2: Write failing controller contract tests**

Extend `EXPECTED_CLOUD_AGENT_PATHS` with:

```python
("POST", "/api/v1/cloud-agent/internal/events"),
("GET", "/api/v1/cloud-agent/events/stream"),
```

Override `get_cloud_job_event_hub` with finite fakes:

```python
class RecordingHub:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


def test_internal_event_intake_accepts_only_safe_model(tmp_path):
    client, _store = _client(tmp_path)
    hub = RecordingHub()
    cloud_agent = _cloud_agent_controller()
    client.app.dependency_overrides[cloud_agent.get_cloud_job_event_hub] = lambda: hub

    response = client.post(
        "/api/v1/cloud-agent/internal/events",
        json=_event_payload(),
    )
    rejected = client.post(
        "/api/v1/cloud-agent/internal/events",
        json={**_event_payload(), "script": "must-not-enter"},
    )

    assert response.status_code == 202
    assert rejected.status_code == 400
    assert len(hub.events) == 1
    assert "script" not in hub.events[0].model_dump(mode="json")


class FiniteStreamHub:
    async def stream(self, *, heartbeat_seconds):
        assert heartbeat_seconds > 0
        yield "event: sync_required\ndata: {}\n\n"


def test_sse_route_sets_streaming_headers_without_unbounded_wait(tmp_path):
    client, _store = _client(tmp_path)
    cloud_agent = _cloud_agent_controller()
    client.app.dependency_overrides[cloud_agent.get_cloud_job_event_hub] = (
        lambda: FiniteStreamHub()
    )

    response = client.get("/api/v1/cloud-agent/events/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
```

- [ ] **Step 3: Run the new tests and observe RED**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_event_hub.py \
  test/services/test_cloud_agent_controller.py -q
```

Expected: collection/route-contract failures because the hub and endpoints do not exist.

- [ ] **Step 4: Implement bounded subscribers and SSE framing**

Use one process-local hub. On stream registration, enqueue a unique
`sync_required` control frame with an opaque `id` and JSON
`{"event_id":"<same-id>"}` data. Use
`asyncio.wait_for(queue.get(), timeout=...)` for heartbeats. On queue full,
discard queued stale state and enqueue a new unique `sync_required` frame;
never await subscriber capacity. Always unregister in `finally`.

Format event data with compact JSON and terminate every frame with two newlines:

```python
return (
    f"id: {event.event_id}\n"
    f"event: {event.type.value}\n"
    f"data: {event.model_dump_json()}\n\n"
)
```

- [ ] **Step 5: Implement endpoints on the existing router**

Create one module-level hub dependency. The async intake awaits `hub.publish`
and returns HTTP 202. The stream route returns:

```python
StreamingResponse(
    hub.stream(
        heartbeat_seconds=float(
            config.app["cloud_agent_sse_heartbeat_seconds"]
        )
    ),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    },
)
```

Do not add media reads, Job writes, provider dependencies, or authentication
logic to these handlers; loopback binding plus Task 5's exact proxy boundary
controls exposure.

- [ ] **Step 6: Run focused API tests and Ruff**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_event_hub.py \
  test/services/test_cloud_agent_controller.py \
  test/services/test_asgi.py -q
uv run ruff check \
  app/services/cloud_agent/event_hub.py \
  app/controllers/v1/cloud_agent.py \
  test/services/cloud_agent/test_event_hub.py \
  test/services/test_cloud_agent_controller.py
```

Expected: all tests and Ruff pass; malformed sensitive payloads are rejected.

- [ ] **Step 7: Commit Task 4**

```bash
git add app/services/cloud_agent/event_hub.py \
  app/controllers/v1/cloud_agent.py \
  test/services/cloud_agent/test_event_hub.py \
  test/services/test_cloud_agent_controller.py
git commit -m "feat: stream cloud job events over sse"
```

---

### Task 5: Expose Only the Authenticated SSE Route

**Files:**
- Create: `deploy/nginx/videosturbo.conf.example`
- Modify: `deploy/cloud-agent/README.md`
- Modify: `test/services/test_cloud_agent_deploy.py`

**Interfaces:**
- Consumes: FastAPI `GET /api/v1/cloud-agent/events/stream` from Task 4.
- Produces: same-origin browser route `/api/v1/cloud-agent/events/stream` proxied to loopback FastAPI with Basic Auth and SSE-safe buffering/timeouts.

- [ ] **Step 1: Write failing proxy-boundary tests**

Extend the deployment test with exact assertions:

```python
NGINX = Path("deploy/nginx/videosturbo.conf.example")


def test_nginx_exposes_only_exact_authenticated_sse_route():
    source = NGINX.read_text(encoding="utf-8")

    assert "location = /api/v1/cloud-agent/events/stream" in source
    assert 'auth_basic "VideosTurbo control panel";' in source
    assert "proxy_pass http://127.0.0.1:8080;" in source
    assert "proxy_buffering off;" in source
    assert "proxy_cache off;" in source
    assert 'proxy_set_header Connection "";' in source
    assert "location /api/" not in source
    assert "/cloud-agent/internal/events" not in source
```

- [ ] **Step 2: Run the deployment test and observe RED**

Run:

```bash
uv run pytest test/services/test_cloud_agent_deploy.py -q
```

Expected: the test fails because the version-controlled Nginx example does not exist.

- [ ] **Step 3: Add the exact SSE location and deployment instructions**

Create a complete server example retaining the existing Streamlit `/` proxy
and add this separate exact location before it:

```nginx
location = /api/v1/cloud-agent/events/stream {
    auth_basic "VideosTurbo control panel";
    auth_basic_user_file /etc/nginx/.videosturbo.htpasswd;

    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600;
    add_header X-Accel-Buffering no always;
}
```

Document that operators copy the exact block into the live authenticated
server, run `sudo nginx -t`, and reload only after validation. Explicitly state
that the internal POST and general `/api/` must not be proxied.

- [ ] **Step 4: Run deployment tests and static checks**

Run:

```bash
uv run pytest test/services/test_cloud_agent_deploy.py -q
git diff --check
```

Expected: tests and whitespace checks pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add deploy/nginx/videosturbo.conf.example \
  deploy/cloud-agent/README.md \
  test/services/test_cloud_agent_deploy.py
git commit -m "docs: define cloud agent sse proxy boundary"
```

---

### Task 6: Replace WebUI Timer Polling with Streamlit Components v2 Events

**Files:**
- Create: `webui/cloud_agent_events.py`
- Create: `test/services/test_cloud_agent_events.py`
- Modify: `webui/cloud_agent.py:13-20,644-681,1502-1625`
- Modify: `webui/cloud_agent_ui.py:180-250,382-384`
- Modify: `test/services/test_cloud_agent_webui.py`
- Modify: `test/services/test_cloud_agent_ui.py`

**Interfaces:**
- Consumes: same-origin SSE events from Task 4/5 and existing `_api`, `_store_job_snapshot`, Production status renderer, and app-scoped rerun.
- Produces:
  - `render_cloud_job_event_listener(stream_url: str, *, key: str) -> Mapping[str, object] | None`.
  - `classify_event(event, *, selected_job_id: str, last_event_id: str) -> Literal["ignore", "refresh_job", "refresh_app", "sync"]`.
  - `_render_event_driven_production_status(...)` with a fragment that has no `run_every` argument.

- [ ] **Step 1: Write failing component and classifier tests**

Create tests that inspect the inline Components v2 contract and exercise the
pure classifier:

```python
def test_event_listener_uses_component_v2_and_closes_event_source():
    source = Path("webui/cloud_agent_events.py").read_text(encoding="utf-8")
    assert "st.components.v2.component" in source
    assert "new EventSource" in source
    assert "setTriggerValue" in source
    assert "return () =>" in source
    assert ".close()" in source
    assert "components.v1" not in source
    assert "setComponentValue" not in source


def test_classifier_refreshes_selected_job_and_app_on_completion():
    assert classify_event(
        {"event_id": "1", "type": "job.updated", "job_id": "job-1"},
        selected_job_id="job-1",
        last_event_id="",
    ) == "refresh_job"
    assert classify_event(
        {"event_id": "2", "type": "job.completed", "job_id": "job-2"},
        selected_job_id="job-1",
        last_event_id="1",
    ) == "refresh_app"
    assert classify_event(
        {"event_id": "2", "type": "job.completed", "job_id": "job-2"},
        selected_job_id="job-1",
        last_event_id="2",
    ) == "ignore"
```

Test `sync_required` returns `sync`, unknown/malformed events return `ignore`,
and unrelated `job.updated` returns `ignore`.

- [ ] **Step 2: Write failing WebUI reconciliation tests**

Replace polling expectations with assertions that:

```python
source = UI_SOURCE.read_text(encoding="utf-8")
assert "LIVE_JOB_REFRESH_SECONDS" not in source
assert "run_every=" not in source
assert "_render_event_driven_production_status" in source
```

Use a fake fragment/component/API to prove one event causes one read:

```python
class EventStreamlit:
    def __init__(self, state):
        self.session_state = state
        self.rerun_scopes = []

    def fragment(self, function=None, **kwargs):
        assert "run_every" not in kwargs
        return function if function is not None else (lambda fn: fn)

    def rerun(self, *, scope):
        self.rerun_scopes.append(scope)


def test_selected_update_reads_job_once_without_loading_video_library(monkeypatch):
    state = {
        "cloud_agent_job_id": "job-1",
        "cloud_agent_job_snapshot": {"id": "job-1", "status": "QUEUED"},
        "cloud_agent_last_event_id": "",
    }
    fake = EventStreamlit(state)
    calls = []
    monkeypatch.setattr(cloud_agent, "st", fake)
    monkeypatch.setattr(
        cloud_agent.cloud_agent_events,
        "render_cloud_job_event_listener",
        lambda *_args, **_kwargs: {
            "event_id": "event-1",
            "type": "job.updated",
            "job_id": "job-1",
        },
    )
    monkeypatch.setattr(
        cloud_agent,
        "_api",
        lambda method, path, **_kw: calls.append((method, path))
        or {"id": "job-1", "status": "TTS_GENERATING"},
    )
    monkeypatch.setattr(
        cloud_agent.cloud_agent_ui,
        "render_production_status",
        lambda *_args, **_kwargs: None,
    )

    cloud_agent._render_event_driven_production_status(
        script_ready=True,
        prepared_voice_ready=False,
        ui_state=state,
    )

    assert calls == [("GET", "jobs/job-1")]
    assert state["cloud_agent_last_event_id"] == "event-1"
    assert fake.rerun_scopes == []
```

Add the selected-completion case explicitly:

```python
def test_selected_completion_stores_snapshot_then_requests_one_app_rerun(monkeypatch):
    state = {
        "cloud_agent_job_id": "job-1",
        "cloud_agent_job_snapshot": {"id": "job-1", "status": "VALIDATING"},
        "cloud_agent_last_event_id": "",
    }
    fake = EventStreamlit(state)
    calls = []
    monkeypatch.setattr(cloud_agent, "st", fake)
    monkeypatch.setattr(
        cloud_agent.cloud_agent_events,
        "render_cloud_job_event_listener",
        lambda *_args, **_kwargs: {
            "event_id": "event-complete",
            "type": "job.completed",
            "job_id": "job-1",
        },
    )
    monkeypatch.setattr(
        cloud_agent,
        "_api",
        lambda method, path, **_kw: calls.append((method, path))
        or {"id": "job-1", "status": "COMPLETED", "checkpoint": "COMPLETED"},
    )
    monkeypatch.setattr(
        cloud_agent.cloud_agent_ui,
        "render_production_status",
        lambda *_args, **_kwargs: None,
    )

    cloud_agent._render_event_driven_production_status(
        script_ready=True,
        prepared_voice_ready=True,
        ui_state=state,
    )

    assert calls == [("GET", "jobs/job-1")]
    assert state["cloud_agent_job_snapshot"]["status"] == "COMPLETED"
    assert state["cloud_agent_last_event_id"] == "event-complete"
    assert fake.rerun_scopes == ["app"]
```

Use the same fixture structure for four more focused tests: a completion event
for `job-2` records `fake.rerun_scopes == ["app"]` while leaving the selected
`job-1` snapshot unchanged; `sync_required` records one `GET jobs/job-1`; a
duplicate `event_id` records no GET/rerun; and a monkeypatched `_api` raising
`requests.ConnectionError` leaves the original snapshot equal to its pre-call
copy and raises no exception.

- [ ] **Step 3: Run the new UI tests and observe RED**

Run:

```bash
uv run pytest \
  test/services/test_cloud_agent_events.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_cloud_agent_ui.py -q
```

Expected: imports/source assertions fail while timed polling is still present.

- [ ] **Step 4: Implement the inline Components v2 EventSource listener**

Register the component once at module import using multiline inline JS. Keep
connections per component root and always clean up:

```javascript
const connections = new WeakMap()

export default function (component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component
  const url = data?.streamUrl ?? ""
  let record = connections.get(parentElement)
  if (record && record.url !== url) {
    record.source.close()
    connections.delete(parentElement)
    record = undefined
  }

  if (!record) {
    record = { url, source: new EventSource(url), setStateValue, setTriggerValue }
    const forward = (message) => {
      try {
        const payload = JSON.parse(message.data || "{}")
        record.setTriggerValue("event", {
          ...payload,
          event_id: payload.event_id || message.lastEventId,
          type: message.type,
        })
      } catch (_error) {
        // Ignore malformed frames; durable reconciliation remains available.
      }
    }
    for (const type of ["job.updated", "job.completed", "sync_required"]) {
      record.source.addEventListener(type, forward)
    }
    record.source.onopen = () => record.setStateValue("connected", true)
    record.source.onerror = () => record.setStateValue("connected", false)
    connections.set(parentElement, record)
  } else {
    record.setStateValue = setStateValue
    record.setTriggerValue = setTriggerValue
  }

  return () => {
    if (connections.get(parentElement) === record) {
      record.source.close()
      connections.delete(parentElement)
    }
  }
}
```

Mount at zero visual height with `on_event_change` and `on_connected_change`
callbacks so `result.event` and `result.connected` are available. Catch JSON
parse errors in JS and ignore malformed frames instead of breaking the
component.

- [ ] **Step 5: Implement event-driven fragment reconciliation**

Rename the current live renderer and retain `@st.fragment` without a timer.
Mount the event listener inside it. Deduplicate `event_id` in
`st.session_state["cloud_agent_last_event_id"]`.

For selected `job.updated`, fetch/store/render the latest Job inside the
fragment. For `job.completed`, fetch/store the selected Job if IDs match, then
call `st.rerun(scope="app")`; the full rerun reloads the library exactly once.
For `sync_required`, perform one selected-Job GET and app-rerun only if the
reconciled state newly became terminal. Render a subtle disconnected caption
without blocking controls.

Remove `LIVE_JOB_REFRESH_SECONDS`, `job_requires_status_refresh`, its tests, and
every `run_every` reference. Do not modify `cloud_agent_worker_poll_seconds`.

- [ ] **Step 6: Run focused UI tests and Ruff**

Run:

```bash
uv run pytest \
  test/services/test_cloud_agent_events.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_cloud_agent_ui.py -q
uv run ruff check \
  webui/cloud_agent_events.py \
  webui/cloud_agent.py \
  webui/cloud_agent_ui.py \
  test/services/test_cloud_agent_events.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_cloud_agent_ui.py
```

Expected: all tests and Ruff pass; source contains no timed active-job polling.

- [ ] **Step 7: Run a local Streamlit component smoke check**

Start the existing app against mocked/local API state only:

```bash
timeout 30s uv run streamlit run webui/Main.py \
  --server.headless=true \
  --server.address=127.0.0.1 \
  --server.port=18501
```

Expected: startup reaches the Streamlit ready message without component
registration errors. During implementation verification, use Playwright
against the local page to confirm one synthetic SSE event triggers one rerun
and component cleanup leaves one EventSource connection.

- [ ] **Step 8: Commit Task 6**

```bash
git add webui/cloud_agent_events.py \
  webui/cloud_agent.py \
  webui/cloud_agent_ui.py \
  test/services/test_cloud_agent_events.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_cloud_agent_ui.py
git commit -m "feat: drive cloud progress from worker events"
```

---

### Task 7: Isolate Every Video Card and the Whole Library from Page Rendering

**Files:**
- Modify: `webui/cloud_agent_ui.py:83-150`
- Modify: `webui/cloud_agent.py:146-188,1542-1546`
- Modify: `test/services/test_cloud_agent_ui.py`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**
- Consumes: existing `load_video(final_url: str) -> bytes` and library renderer callbacks.
- Produces: `_render_video_card_media(card, *, load_video) -> None` with per-card failure containment; `_render_video_library` with an outer unexpected-error boundary that returns control to the page.

- [ ] **Step 1: Write failing per-card continuation tests**

Create a two-card view. Make the first loader raise `requests.ConnectionError`
and the second return valid bytes. Assert the first placeholder and second
video both render:

```python
def test_failed_video_card_shows_placeholder_and_later_card_renders(monkeypatch):
    fake = LibraryStreamlit()
    monkeypatch.setattr(cloud_agent_ui, "st", fake)

    def load(url):
        if url.endswith("job-1/final"):
            raise requests.ConnectionError("temporary")
        return b"job-2-video"

    cloud_agent_ui.render_video_library(
        _two_card_view(),
        load_video=load,
        pending_delete_id="",
        on_delete_request=lambda _job_id: None,
        on_delete_confirm=lambda _job_id: None,
        on_delete_cancel=lambda _job_id: None,
        on_page=lambda _page: None,
    )

    assert "วิดีโอนี้โหลดไม่ได้ กรุณาลองใหม่ภายหลัง" in fake.warnings
    assert fake.videos == [b"job-2-video"]
```

Parameterize timeout, 404/HTTP error, empty bytes, invalid URL/`ValueError`, and
an unexpected loader exception. The result is always the card placeholder and
continued rendering.

- [ ] **Step 2: Write a failing whole-library boundary test**

Patch the renderer to fail and prove the orchestration function returns control
to its caller:

```python
def test_unexpected_library_renderer_failure_returns_control_to_page(monkeypatch):
    state = {}
    fake = _VideoLibraryStreamlit(state)
    continued = []
    monkeypatch.setattr(cloud_agent, "st", fake)
    monkeypatch.setattr(cloud_agent, "_load_video_library", lambda _page: _video_page())
    monkeypatch.setattr(
        cloud_agent.cloud_agent_ui,
        "render_video_library",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("renderer")),
    )
    monkeypatch.setattr(
        cloud_agent,
        "_delete_video",
        lambda *_args, **_kwargs: pytest.fail("must not delete or retry"),
    )

    cloud_agent._render_video_library(ui_state=state)
    continued.append("job-controls")

    assert continued == ["job-controls"]
    assert fake.errors == [
        "ไม่สามารถแสดงรายการวิดีโอได้ในขณะนี้ กรุณาลองใหม่ภายหลัง"
    ]
```

Keep provider/Flow/Canva/TTS fakes absent from this unit: the library boundary
has no imports or dependencies that can reach them, and the full regressions in
Step 5 prove those subsystems remain unchanged.

- [ ] **Step 3: Run the boundary tests and observe RED**

Run:

```bash
uv run pytest \
  test/services/test_cloud_agent_ui.py -k "failed_video_card" \
  test/services/test_cloud_agent_webui.py -k "library" -q
```

Expected: the first loader exception escapes and aborts later rendering.

- [ ] **Step 4: Implement the smallest two-level error boundary**

In the card renderer, treat empty bytes as unavailable and catch the loader and
`st.video` boundary per card:

```python
try:
    media = load_video(card.final_url)
    if not media:
        raise ValueError("empty video media")
    st.video(media, format="video/mp4")
except Exception as exc:
    logger.warning(
        "Cloud video card unavailable: job_id={}, error={}",
        card.job_id,
        type(exc).__name__,
    )
    st.warning("วิดีโอนี้โหลดไม่ได้ กรุณาลองใหม่ภายหลัง")
```

Do not log the URL, response body, local path, or exception message. Continue
rendering metadata/delete controls so the card remains manageable.

Wrap the library invocation at the page orchestration boundary. On unexpected
failure, log only the exception class and render
**ไม่สามารถแสดงรายการวิดีโอได้ในขณะนี้ กรุณาลองใหม่ภายหลัง**, then proceed to
Job controls and Production status.

- [ ] **Step 5: Run all Cloud Agent UI/library regressions and Ruff**

Run:

```bash
uv run pytest \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_cloud_agent_video_library.py \
  test/services/test_cloud_agent_controller.py -q
uv run ruff check webui/cloud_agent.py webui/cloud_agent_ui.py \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_webui.py
```

Expected: all tests and Ruff pass; one failed video cannot hide later cards or Job controls.

- [ ] **Step 6: Commit Task 7**

```bash
git add webui/cloud_agent.py webui/cloud_agent_ui.py \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_webui.py
git commit -m "fix: isolate cloud video library rendering"
```

---

### Task 8: Integrated Verification, Review, and Deployment Gate

**Files:**
- Modify only if verification exposes a requirement gap: files owned by Tasks 1-7 and their focused tests.
- Verify: `/etc/nginx/sites-available/videosturbo` only after all code reviews pass and deployment is explicitly authorized.

**Interfaces:**
- Consumes: all Tasks 1-7.
- Produces: reviewed branch with full automated evidence and a deployment checklist; no production mutation occurs before the final gate.

- [ ] **Step 1: Run the complete focused Cloud Agent suite**

```bash
uv run pytest \
  test/services/cloud_agent \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_deploy.py \
  test/services/test_cloud_agent_events.py \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_video_library.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_config.py \
  test/services/test_asgi.py -q
```

Expected: all tests pass with no external provider/browser calls.

- [ ] **Step 2: Run repository static and regression gates**

```bash
uv run ruff check app webui test
git diff --check
uv run pytest -q
```

Expected: Ruff, whitespace checks, and the entire repository suite pass.

- [ ] **Step 3: Perform two-stage final review**

Dispatch a fresh spec-compliance reviewer first. It must compare every Global
Constraint and Spec section against the diff and test evidence. After all
compliance findings are resolved, dispatch a separate code-quality reviewer
focused on concurrency, queue shutdown, SSE subscriber cleanup, secret/path
leakage, Streamlit rerun loops, duplicate events, Nginx exposure, and media
exception containment. Any fix returns through its focused RED/GREEN test and
the relevant regression commands before re-review.

- [ ] **Step 4: Record the final branch evidence**

```bash
git status --short
git log --oneline --decorate -12
git diff checkpoint-before-video-card-media-isolation..HEAD --stat
```

Expected: clean worktree and independently reviewable Task commits. Do not
merge, push, restart, or deploy at this step.

- [ ] **Step 5: Apply the deployment gate only after explicit approval**

After the user authorizes deployment:

1. create a fresh checkpoint tag/commit;
2. update only the exact SSE block in `/etc/nginx/sites-available/videosturbo`;
3. run `sudo nginx -t` before `sudo systemctl reload nginx`;
4. restart API, Worker, and WebUI services in that order;
5. verify each service is active and inspect bounded recent logs;
6. open one protected Cloud Agent page and confirm exactly one SSE connection;
7. send safe synthetic `job.updated` and `job.completed` signals through the
   loopback intake for an existing Job and confirm one reconciliation per event;
8. confirm an existing `COMPLETED` record has populated `completed_at` and
   appears in **วีดีโอที่สร้าง** after the completion signal;
9. stop only the SSE delivery path briefly, confirm Production state remains
   unchanged, restore it, and confirm reconnect `sync_required` reconciles the
   page; and
10. restore services and report exact commit/deployment state. Queue a new live
    production Job only if the user separately authorizes the provider/browser
    work and any associated credits.

Do not expose the internal POST, do not run a real provider smoke test without
the user's explicit paid-operation authorization, and do not delete any Job or
video during deployment verification.
