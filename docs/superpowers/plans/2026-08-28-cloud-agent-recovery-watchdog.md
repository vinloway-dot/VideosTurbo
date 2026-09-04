# Cloud Agent Recovery Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover one missing Google Flow clip with at most two fenced targeted submissions, restart stalled Canva assembly at most four times, and delete one-hour-stalled Jobs locally while preserving a safe WebUI incident.

**Architecture:** Keep the existing successful workflow path intact. Add durable recovery state and milestones in SQLite, isolate browser automation in a per-Job child process supervised by the existing Worker, and use deterministic archive validation instead of Agent chat as the source of truth. Extend the existing non-blocking SSE path with durable incident invalidation while keeping provider and WebUI failures isolated.

**Tech Stack:** Python 3.11, Pydantic, SQLite/WAL, Playwright, multiprocessing, FastAPI, Streamlit Components v2, pytest, Ruff, systemd, Nginx SSE.

**Spec:** `docs/superpowers/specs/2026-08-28-cloud-agent-recovery-watchdog-design.md`

## Global Constraints

- Rollback tag: `rollback/cloud-agent-before-recovery-20260828` -> commit `8fe78986f637edd23521778046608c4b516e64e5`.
- Verified host snapshot: `storage/rollback-checkpoints/20260828T140304Z-8fe7898`; never modify or commit this ignored directory.
- Normal Flow remains one six-clip batch; only exactly one proven missing clip is recoverable.
- Flow targeted replacement budget is exactly `2` additional paid submissions.
- Canva restart budget is exactly `4` additional attempts after the initial attempt.
- Canva inactivity deadline is `20 minutes`; global active-Job inactivity deadline is `60 minutes`.
- A queued Job waiting for a Worker is not timed by the global watchdog.
- Heartbeats, leases, `updated_at`, process liveness, repeated observations, and SSE keep-alives are not meaningful progress.
- Targeted replacement uses the matching stored `video_prompt` verbatim.
- The latest complete Project archive is preferred; merging five prior clips with one later replacement is fallback-only and must be atomic.
- Do not poll active Jobs from the WebUI every two seconds.
- Do not retry Research, TTS, or the original Flow batch during Canva restart.
- Do not delete remote Google Flow or Canva projects automatically.
- Do not expose prompts, scripts, cookies, keys, local paths, provider payloads, or raw exceptions in incidents/events.
- Every paid submission counter/fence is committed before clicking Generate.
- Every retry or deletion starts only after the previous Job child process group is confirmed stopped.
- All schema migrations are additive and backward compatible with rollback commit `8fe7898`.

---

## File Structure

### New focused modules

- `app/services/cloud_agent/progress.py`: durable meaningful-progress reporter and child-to-supervisor progress signals.
- `app/services/cloud_agent/flow_recovery.py`: exact-prompt selection, persisted replacement state machine, retry budget, and reconciliation.
- `app/services/cloud_agent/incidents.py`: sanitized incident persistence, terminal local cleanup coordination, and DTOs.
- `app/services/cloud_agent/worker_process.py`: production child launcher/process-group termination and child entry point.
- `test/services/cloud_agent/test_progress.py`: milestone semantics.
- `test/services/cloud_agent/test_flow_recovery.py`: recovery coordinator and paid-fence behavior.
- `test/services/cloud_agent/test_incidents.py`: incident persistence and two-phase local deletion.
- `test/services/cloud_agent/test_worker_process.py`: child-process and termination isolation.

### Existing modules changed at their current responsibility boundary

- `app/models/cloud_agent.py`: recovery enums/fields and public incident model.
- `app/services/cloud_agent/job_store.py`: additive Job columns and atomic reservation methods.
- `app/services/cloud_agent/storage.py`: attempt-specific Flow snapshot paths and final-artifact quarantine.
- `app/services/cloud_agent/flow_archive.py`: partial archive inspection and atomic merge fallback.
- `app/services/cloud_agent/errors.py`: typed incomplete-batch/recovery errors.
- `app/services/cloud_agent/providers/google_flow.py`: scoped failed-card detection, survivor snapshot, targeted submit, and remote reconciliation.
- `app/services/cloud_agent/providers/canva.py`: verified milestone callbacks only; no policy/retry counters.
- `app/services/cloud_agent/workflow.py`: delegate Flow recovery, preserve `FLOW_READY` across Canva retries, and report milestones.
- `app/services/cloud_agent/worker.py`: supervisor event loop, lease ownership, deadlines, and retry/delete decisions.
- `app/services/cloud_agent/job_events.py`: sanitized incident event model.
- `app/services/cloud_agent/event_hub.py`: accept the safe event union without changing overflow behavior.
- `app/services/cloud_agent/factory.py`: compose child launcher, reporter, recovery, incidents, and supervisor.
- `app/controllers/v1/cloud_agent.py`: unread incident and dismiss endpoints plus event intake union.
- `webui/cloud_agent_events.py`: recognize `job.incident`.
- `webui/cloud_agent.py`: one incident read on load/reconnect/event and Thai banner.
- `app/config/config.py` and `config.example.toml`: bounded operational guardrails.
- Existing Cloud Agent tests under `test/services/cloud_agent/` and WebUI/controller tests under `test/services/`.

---

### Task 1: Add Durable Recovery and Incident State

**Files:**
- Modify: `app/models/cloud_agent.py`
- Modify: `app/services/cloud_agent/job_store.py`
- Modify: `app/services/cloud_agent/errors.py`
- Create: `app/services/cloud_agent/incidents.py`
- Modify: `test/services/cloud_agent/test_models.py`
- Modify: `test/services/cloud_agent/test_job_store.py`
- Create: `test/services/cloud_agent/test_incidents.py`

**Interfaces:**
- Consumes: existing `CloudJobRecord`, `CloudJobStore.patch_job()`, SQLite migration pattern.
- Produces: `FlowRecoveryState`, `CloudJobIncident`, recovery fields on `CloudJobRecord`, `CloudJobStore.mark_progress()`, `reserve_flow_recovery_attempt()`, `reserve_canva_restart()`, and `CloudJobIncidentStore` CRUD/terminal transaction methods.

- [ ] **Step 1: Write failing model and migration tests**

```python
def test_old_database_migrates_recovery_fields_with_safe_defaults(tmp_path):
    store = create_legacy_store_then_reopen(tmp_path)
    job = store.list_jobs()[0]
    assert job.flow_recovery_state is FlowRecoveryState.NONE
    assert job.flow_recovery_attempts == 0
    assert job.flow_missing_clip_index == 0
    assert job.canva_restart_attempts == 0
    assert job.last_progress_at == ""
    assert job.last_progress_milestone == ""

def test_incident_rejects_sensitive_extra_fields():
    with pytest.raises(ValueError):
        CloudJobIncident.model_validate({**valid_incident(), "script": "secret"})
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/pytest test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/cloud_agent/test_incidents.py -q`

Expected: collection/import failures for undefined recovery and incident types.

- [ ] **Step 3: Add exact enums, fields, and additive SQLite columns**

```python
class FlowRecoveryState(str, Enum):
    NONE = "NONE"
    INVENTORY_PENDING = "INVENTORY_PENDING"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMISSION_UNRESOLVED = "SUBMISSION_UNRESOLVED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"

class RecoveryBudgetExhausted(RuntimeError):
    pass

class CloudJobIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    former_job_id: str
    subject: str
    stage: str
    reason_code: str
    flow_attempts: int = Field(ge=0, le=2)
    canva_attempts: int = Field(ge=0, le=4)
    message_th: str
    created_at: str
    dismissed_at: str = ""
    finalized: bool = False
```

Add `last_progress_at`, `last_progress_milestone`, `stage_started_at`, `flow_recovery_attempts`, `flow_missing_clip_index`, `flow_recovery_state`, `flow_recovery_baseline`, `canva_restart_attempts`, and `canva_attempt_started_at` to `_COMPATIBLE_COLUMNS`, `_MUTABLE_COLUMNS`, table creation, inserts, row mapping, and `CloudJobRecord`.

- [ ] **Step 4: Add atomic store methods and budget tests**

```python
def test_flow_attempt_is_reserved_before_caller_can_submit(tmp_path):
    store, job = store_and_job(tmp_path)
    reserved = store.reserve_flow_recovery_attempt(job.id, missing_index=2)
    assert reserved.flow_recovery_attempts == 1
    assert reserved.flow_recovery_state is FlowRecoveryState.SUBMISSION_UNRESOLVED
    assert store.get_job(job.id) == reserved

def test_canva_restart_budget_stops_after_four(tmp_path):
    store, job = store_and_job(tmp_path, checkpoint=CloudJobCheckpoint.FLOW_READY)
    for expected in range(1, 5):
        assert store.reserve_canva_restart(job.id).canva_restart_attempts == expected
    with pytest.raises(RecoveryBudgetExhausted):
        store.reserve_canva_restart(job.id)
```

Implement reservations with `BEGIN IMMEDIATE`, a conditional count check, and one committed update so concurrent workers cannot reserve the same attempt.

- [ ] **Step 5: Implement incident persistence and terminal transaction**

`CloudJobIncidentStore` must expose:

```python
def create_pending(self, job: CloudJobRecord, *, reason_code: str, stage: str, message_th: str) -> CloudJobIncident:
    raise NotImplementedError
def list_unread(self, *, limit: int = 20) -> tuple[CloudJobIncident, ...]:
    raise NotImplementedError
def dismiss(self, incident_id: str) -> CloudJobIncident:
    raise NotImplementedError
def finalize_and_delete_job(self, incident_id: str, job_id: str) -> CloudJobIncident:
    raise NotImplementedError
```

`finalize_and_delete_job` updates the incident and deletes the Job in one SQLite transaction. It must fail if the incident is missing, already finalized for another Job, or the Job is still claimable.

- [ ] **Step 6: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/cloud_agent/test_incidents.py -q`

Expected: PASS.

Run: `.venv/bin/ruff check app/models/cloud_agent.py app/services/cloud_agent/job_store.py app/services/cloud_agent/errors.py app/services/cloud_agent/incidents.py test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/cloud_agent/test_incidents.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models/cloud_agent.py app/services/cloud_agent/job_store.py app/services/cloud_agent/errors.py app/services/cloud_agent/incidents.py test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py test/services/cloud_agent/test_incidents.py
git commit -m "feat: persist cloud recovery state and incidents"
```

---

### Task 2: Record Meaningful Progress Without WebUI Polling

**Files:**
- Create: `app/services/cloud_agent/progress.py`
- Modify: `app/services/cloud_agent/job_events.py`
- Create: `test/services/cloud_agent/test_progress.py`
- Modify: `test/services/cloud_agent/test_job_events.py`

**Interfaces:**
- Consumes: `CloudJobStore.mark_progress()` and existing event-publishing store projection.
- Produces: `ProgressSignal(job_id, milestone, occurred_at)`, `ProgressSignalSink`, `ProgressReporter.reached(job_id, milestone)`, and `DurableProgressReporter`.

- [ ] **Step 1: Write failing milestone tests**

```python
def test_new_milestone_advances_timestamp_and_signals_once(tmp_path, clock):
    store, job = store_and_job(tmp_path)
    sink = RecordingProgressSink()
    reporter = DurableProgressReporter(store, sink=sink, clock=clock)
    first = reporter.reached(job.id, "flow.inventory.5")
    clock.advance(seconds=30)
    same = reporter.reached(job.id, "flow.inventory.5")
    assert same.last_progress_at == first.last_progress_at
    assert [item.milestone for item in sink.items] == ["flow.inventory.5"]

def test_timestamp_only_progress_does_not_emit_job_updated(tmp_path):
    event_sink, store, job = event_store_and_job(tmp_path)
    store.mark_progress(job.id, "canva.audio.inserted")
    assert event_sink.events == []
```

- [ ] **Step 2: Run RED tests**

Run: `.venv/bin/pytest test/services/cloud_agent/test_progress.py test/services/cloud_agent/test_job_events.py -q`

Expected: FAIL because progress interfaces do not exist.

- [ ] **Step 3: Implement the reporter and safe sink boundary**

```python
@dataclass(frozen=True)
class ProgressSignal:
    job_id: str
    milestone: str
    occurred_at: str

class Clock(Protocol):
    def now(self) -> datetime:
        raise NotImplementedError

class ProgressReporter(Protocol):
    def reached(self, job_id: str, milestone: str) -> CloudJobRecord:
        raise NotImplementedError

class ProgressSignalSink(Protocol):
    def publish_nowait(self, signal: ProgressSignal) -> bool:
        raise NotImplementedError

class DurableProgressReporter:
    def reached(self, job_id: str, milestone: str) -> CloudJobRecord:
        before = self._store.get_job(job_id)
        after = self._store.mark_progress(job_id, milestone, at=self._clock.now())
        if before and after.last_progress_at != before.last_progress_at:
            try:
                self._sink.publish_nowait(ProgressSignal(job_id, milestone, after.last_progress_at))
            except Exception:
                logger.warning("cloud progress signal dropped job_id={}", job_id)
        return after
```

Normalize milestone identifiers to a non-empty maximum of 128 safe ASCII characters. Sink failure must never roll back the committed timestamp.

- [ ] **Step 4: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_progress.py test/services/cloud_agent/test_job_events.py -q`

Run: `.venv/bin/ruff check app/services/cloud_agent/progress.py app/services/cloud_agent/job_events.py test/services/cloud_agent/test_progress.py test/services/cloud_agent/test_job_events.py`

Expected: PASS for both.

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/progress.py app/services/cloud_agent/job_events.py test/services/cloud_agent/test_progress.py test/services/cloud_agent/test_job_events.py
git commit -m "feat: track meaningful cloud job progress"
```

---

### Task 3: Inspect Partial Flow Archives and Materialize Recovery Atomically

**Files:**
- Modify: `app/services/cloud_agent/storage.py`
- Modify: `app/services/cloud_agent/flow_archive.py`
- Modify: `test/services/cloud_agent/test_storage.py`
- Modify: `test/services/cloud_agent/test_flow_archive.py`

**Interfaces:**
- Consumes: existing safe ZIP-member validation, `validate_flow_source_video()`, `JobPaths.flow_staging_dir`, and atomic canonical materialization.
- Produces: `JobPaths.flow_snapshots_dir`, `FlowPartialInventory`, `inspect_partial_flow_archive()`, `materialize_latest_or_merge_recovery()`.

- [ ] **Step 1: Write failing partial-inventory tests**

```python
def test_five_semantic_clips_produce_one_missing_index(tmp_path, media_probe):
    archive, paths = partial_archive(tmp_path, numbers=(1, 3, 4, 5, 6))
    result = inspect_partial_flow_archive(archive, paths, min_size_bytes=1)
    assert result.missing_index == 2
    assert result.semantic_numbers == (1, 3, 4, 5, 6)
    assert len(result.baseline_digest) == 64

@pytest.mark.parametrize("numbers", [(1, 2, 3, 4), (1, 2, 2, 4, 5), (1, 2, 3, 4, 5, 7)])
def test_partial_inventory_rejects_non_exact_safe_five(numbers, tmp_path):
    with pytest.raises(FlowArchiveValidationError):
        inspect_partial_flow_archive(build_archive(tmp_path, numbers), paths(tmp_path), min_size_bytes=1)
```

- [ ] **Step 2: Write failing latest-archive and merge tests**

```python
def test_complete_second_archive_wins_without_reading_first_files(tmp_path):
    result = materialize_latest_or_merge_recovery(complete_second_zip, prior_inventory, paths, validation)
    assert result.source == "latest_complete_archive"
    assert tuple(path.read_bytes() for path in paths.flow_files) == second_payloads

def test_replacement_only_merge_is_atomic_on_validation_failure(tmp_path):
    seed_canonical_files(paths, b"original")
    with pytest.raises(FlowArchiveValidationError):
        materialize_latest_or_merge_recovery(invalid_replacement_zip, prior_inventory, paths, validation)
    assert all(path.read_bytes() == b"original" for path in paths.flow_files)
```

- [ ] **Step 3: Run RED tests**

Run: `.venv/bin/pytest test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_flow_archive.py -q`

Expected: FAIL for missing snapshot paths and recovery functions.

- [ ] **Step 4: Add attempt-specific storage paths**

Add `flow_snapshots_dir = flow/downloads/snapshots` to `JobPaths` and `prepare()`. Add a validator-backed helper:

```python
def flow_snapshot_path(self, job_id: str, *, phase: Literal["partial", "replacement"], attempt: int) -> Path:
    if attempt < 0 or attempt > 2:
        raise ValueError("invalid Flow snapshot attempt")
    return self.prepare(job_id).flow_snapshots_dir / f"{phase}-{attempt}.zip"
```

- [ ] **Step 5: Implement separate partial and final validators**

`FlowPartialInventory` contains snapshot path, five semantic numbers, missing index, extracted immutable staging paths, and a SHA-256 baseline digest over semantic number plus validated file digest. Do not weaken `_semantic_members()`; it must still require complete `1..6` for final materialization.

`materialize_latest_or_merge_recovery()` first calls existing complete archive materialization on a new staging set. Only if the later archive is proven to contain exactly the missing semantic MP4 may it combine that file with the five immutable staged survivors. It revalidates all six and atomically replaces canonical files last.

- [ ] **Step 6: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_flow_archive.py -q`

Run: `.venv/bin/ruff check app/services/cloud_agent/storage.py app/services/cloud_agent/flow_archive.py test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_flow_archive.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/cloud_agent/storage.py app/services/cloud_agent/flow_archive.py test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_flow_archive.py
git commit -m "feat: validate partial Flow recovery archives"
```

---

### Task 4: Detect a Failed Flow Card and Expose Targeted Provider Actions

**Files:**
- Modify: `app/services/cloud_agent/errors.py`
- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`

**Interfaces:**
- Consumes: `FlowPartialInventory`, attempt snapshot paths, current Agent composer, project download action.
- Produces: `FlowBatchIncompleteError`, `FlowRecoveryObservation`, `capture_partial_inventory()`, `submit_targeted_replacement()`, `reconcile_targeted_replacement()`, `download_recovery_snapshot()` on `FlowWorkspaceRun`.

- [ ] **Step 1: Write failing fast-failure tests**

```python
def test_visible_failed_output_card_stops_wait_before_generation_timeout(monkeypatch):
    page = FakePage(
        progress_html=["<main>Generating</main>"],
        clip_names=[f"clip {number}" for number in range(1, 6)],
        failed_output_card_texts=["Audio Generation Failed"],
    )
    client, _sessions = _client(page, timeout_seconds=1800)
    sleep_calls = []
    monkeypatch.setattr(google_flow.time, "sleep", sleep_calls.append)
    with pytest.raises(FlowBatchIncompleteError) as error:
        client._wait_for_generation(page, expected_count=6)
    assert error.value.completed_count == 5
    assert error.value.failed_count == 1
    assert sleep_calls == []

def test_agent_panel_failure_text_does_not_count_as_failed_output_card():
    page = FakePage(
        progress_html=["<aside>Audio Generation Failed</aside>"],
        clip_names=[f"clip {number}" for number in range(1, 6)],
        failed_output_card_texts=[],
    )
    assert client._failed_output_card_count(page) == 0
```

- [ ] **Step 2: Write failing targeted-action contract tests**

```python
def test_targeted_submit_uses_exact_prepared_prompt_and_one_generate_click():
    workspace.prepare_targeted_replacement(exact_wrapper, missing_index=2)
    workspace.submit_targeted_replacement(exact_wrapper, missing_index=2)
    assert page.agent_prompt_value == exact_wrapper
    assert page.generate_clicks == 1

def test_reconcile_never_clicks_generate():
    result = workspace.reconcile_targeted_replacement(job, paths, missing_index=2, attempt=1)
    assert result.state in set(FlowRecoveryRemoteState)
    assert page.generate_clicks == 0
```

- [ ] **Step 3: Run RED tests**

Run: `.venv/bin/pytest test/services/cloud_agent/test_google_flow.py -q`

Expected: new failed-card and targeted action tests FAIL.

- [ ] **Step 4: Implement card-scoped failure detection**

Inspect only the output-card accessible subtree. Require an observable output card with a recognized failure state; do not search the entire page, Agent chat, hidden elements, or transient loading text. Raise `FlowBatchIncompleteError(completed_count=5, failed_count=1)` only after the failed card is stable and no longer busy.

- [ ] **Step 5: Implement narrow provider actions**

Define the provider-only observation contract exactly:

```python
class FlowRecoveryRemoteState(str, Enum):
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    COMPLETE_PROJECT = "COMPLETE_PROJECT"
    REPLACEMENT_ONLY = "REPLACEMENT_ONLY"
    AMBIGUOUS = "AMBIGUOUS"

@dataclass(frozen=True)
class FlowRecoveryObservation:
    state: FlowRecoveryRemoteState
    snapshot_path: Path | None = None
```

Add methods with no SQLite writes:

```python
def capture_partial_inventory(self, paths: JobPaths, *, attempt: int) -> FlowPartialInventory:
    raise NotImplementedError
def prepare_targeted_replacement(self, prompt: str, *, missing_index: int) -> AgentComposer:
    raise NotImplementedError
def submit_targeted_replacement(self, prompt: str, *, missing_index: int) -> None:
    raise NotImplementedError
def reconcile_targeted_replacement(self, paths: JobPaths, *, missing_index: int, attempt: int) -> FlowRecoveryObservation:
    raise NotImplementedError
def download_recovery_snapshot(self, paths: JobPaths, *, attempt: int) -> Path:
    raise NotImplementedError
```

Before the partial download, submit the approved survivor-renaming instruction, wait for response, reload, and corroborate the semantic gap with stable output-slot order or provider-visible request/card metadata. Return `AMBIGUOUS` if corroboration is unavailable.

- [ ] **Step 6: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_google_flow.py -q`

Run: `.venv/bin/ruff check app/services/cloud_agent/errors.py app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/cloud_agent/errors.py app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py
git commit -m "feat: expose bounded Flow clip recovery actions"
```

---

### Task 5: Orchestrate Exact-Prompt Flow Recovery Behind Paid Fences

**Files:**
- Create: `app/services/cloud_agent/flow_recovery.py`
- Modify: `app/services/cloud_agent/workflow.py`
- Create: `test/services/cloud_agent/test_flow_recovery.py`
- Modify: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**
- Consumes: Task 1 reservations/state, Task 2 reporter, Task 3 archive inventory/materialization, Task 4 provider actions.
- Produces: `build_targeted_replacement_prompt(job, missing_index)`, `FlowRecoveryCoordinator.recover_incomplete_batch()`, `resume_unresolved_recovery()`, and workflow delegation.

- [ ] **Step 1: Write exact-prompt and missing-index tests**

```python
def test_targeted_wrapper_contains_stored_prompt_verbatim(job):
    original = job.clip_plan.segments[1].video_prompt
    prompt = build_targeted_replacement_prompt(job, 2)
    assert prompt.endswith(original)
    assert prompt.count(original) == 1
    assert 'Name only the new completed video "clip 2"' in prompt

def test_missing_index_without_exact_segment_is_rejected(job):
    with pytest.raises(FlowRecoveryMappingError):
        build_targeted_replacement_prompt(job, 7)
```

- [ ] **Step 2: Write paid-fence and restart tests**

```python
def test_attempt_is_durable_before_paid_submit(coordinator, recording_workspace, store, job):
    coordinator.recover_incomplete_batch(job, recording_workspace, paths)
    assert recording_workspace.events[:2] == ["reserve_attempt_1_committed", "submit_clip_2"]

def test_unresolved_attempt_reconciles_without_second_submit(coordinator, recording_workspace, store, job):
    seed_state(store, job, state=FlowRecoveryState.SUBMISSION_UNRESOLVED, attempts=1, missing=2)
    coordinator.resume_unresolved_recovery(job, recording_workspace, paths)
    assert recording_workspace.submit_calls == 0
    assert recording_workspace.reconcile_calls == 1

def test_failed_replacement_submits_at_most_two_times(coordinator, always_failed_workspace, store, job, paths):
    with pytest.raises(FlowRecoveryExhausted) as error:
        coordinator.recover_incomplete_batch(job, always_failed_workspace, paths)
    assert always_failed_workspace.submit_calls == 2
    assert error.value.error_code == "FLOW_RECOVERY_EXHAUSTED"
```

- [ ] **Step 3: Run RED tests**

Run: `.venv/bin/pytest test/services/cloud_agent/test_flow_recovery.py test/services/cloud_agent/test_workflow.py -q`

Expected: FAIL for missing coordinator and workflow recovery branch.

- [ ] **Step 4: Implement the recovery state machine**

Define coordinator failures in `flow_recovery.py` with stable codes:

```python
class FlowRecoveryMappingError(RuntimeError):
    error_code = "FLOW_MISSING_CLIP_UNRESOLVED"

class FlowRecoveryExhausted(RuntimeError):
    error_code = "FLOW_RECOVERY_EXHAUSTED"
```

Required transition order:

```text
initial batch conclusively incomplete
-> resolve flow_generation_unresolved=false
-> INVENTORY_PENDING
-> validate five survivors and persist missing index/baseline
-> READY_TO_SUBMIT
-> atomically reserve attempt and SUBMISSION_UNRESOLVED
-> paid Generate click
-> reconcile RUNNING / FAILED / COMPLETE / AMBIGUOUS
-> VERIFICATION_PENDING
-> fresh full archive or strict merge fallback
-> canonical six clips valid
-> NONE and normal FLOW_READY transition
```

An ambiguous result raises a typed terminal recovery error without another submission. Failed attempt 1 returns to `READY_TO_SUBMIT`; failed attempt 2 raises `FLOW_RECOVERY_EXHAUSTED`.

- [ ] **Step 5: Integrate workflow ordering**

At checkpoint `TTS_READY`, order branches as:

1. recover already-valid local canonical artifacts;
2. resume non-`NONE` targeted recovery state;
3. reconcile original `flow_generation_unresolved` batch;
4. otherwise run the normal prepared batch path.

Catch `FlowBatchIncompleteError` from original generation/reconciliation and delegate to the coordinator. Preserve all existing checkpoint validations and cleanup semantics.

- [ ] **Step 6: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_flow_recovery.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_flow_archive.py test/services/cloud_agent/test_google_flow.py -q`

Run: `.venv/bin/ruff check app/services/cloud_agent/flow_recovery.py app/services/cloud_agent/workflow.py test/services/cloud_agent/test_flow_recovery.py test/services/cloud_agent/test_workflow.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/cloud_agent/flow_recovery.py app/services/cloud_agent/workflow.py test/services/cloud_agent/test_flow_recovery.py test/services/cloud_agent/test_workflow.py
git commit -m "feat: recover one missing Flow clip safely"
```

---

### Task 6: Emit Canva Milestones and Preserve Restartable FLOW_READY State

**Files:**
- Modify: `app/services/cloud_agent/providers/canva.py`
- Modify: `app/services/cloud_agent/workflow.py`
- Modify: `app/services/cloud_agent/storage.py`
- Modify: `test/services/cloud_agent/test_canva.py`
- Modify: `test/services/cloud_agent/test_workflow.py`
- Modify: `test/services/cloud_agent/test_storage.py`

**Interfaces:**
- Consumes: `ProgressReporter`, existing audio-first Canva sequence, `FLOW_READY` checkpoint.
- Produces: optional `progress: Callable[[str], None]` on Canva assembly, verified Canva milestone identifiers, and safe removal/quarantine of stale partial final output before a restart.

- [ ] **Step 1: Write failing milestone-order tests**

```python
def test_canva_reports_only_verified_postconditions(fake_page, media_files):
    milestones = []
    client.assemble_and_export(job, clips, audio, output, progress=milestones.append)
    assert milestones == [
        "canva.timeline.cleared",
        "canva.uploads.ready",
        "canva.audio.inserted",
        *[f"canva.video.inserted.{n}" for n in range(1, 7)],
        "canva.source_audio.muted",
        "canva.captions.requested",
        "canva.captions.stable",
        "canva.export.started",
        "canva.export.downloaded",
    ]

def test_failed_postcondition_does_not_emit_its_milestone(tmp_path):
    page = FakeCaptionStylePage(caption_ready_after_seconds=91.0)
    client, _sessions = _assembly_client(page)
    clips, audio, output = _media(tmp_path)
    milestones = []
    with pytest.raises(CanvaUIVerificationError):
        client.assemble_and_export(
            _assembly_job(), clips, audio, output, progress=milestones.append
        )
    assert "canva.captions.stable" not in milestones
```

- [ ] **Step 2: Write restart-safety tests**

```python
def test_canva_resume_from_flow_ready_reuses_sources_and_does_not_call_tts_or_flow(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare(job.id)
    paths.voice_file.write_bytes(b"voice")
    for path in paths.flow_files:
        path.write_bytes(b"clip")
    store.patch_job(
        job.id,
        status=CloudJobStatus.FLOW_READY,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="flow_ready",
        voice_file=str(paths.voice_file),
        canva_restart_attempts=1,
    )
    tts, flow, canva = RecordingTTS(), RecordingFlow(), RecordingCanva()
    _patch_timed_media(monkeypatch, audio_duration=60.0, final_duration=60.0)
    workflow = _workflow(tmp_path, store, tts=tts, flow=flow, canva=canva)
    workflow.run(job.id, worker_id="child-2")
    assert tts.calls == 0
    assert flow.calls == 0
    assert canva.calls == 1

def test_stale_partial_final_is_quarantined_before_canva_retry(tmp_path):
    paths.final_file.write_bytes(b"partial")
    quarantined = storage.quarantine_partial_final(job_id)
    assert quarantined.is_file()
    assert not paths.final_file.exists()
```

- [ ] **Step 3: Run RED tests**

Run: `.venv/bin/pytest test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_storage.py -q`

- [ ] **Step 4: Add verified callback points without moving policy into Canva provider**

Invoke the callback only after each existing postcondition succeeds. Keep start-at-zero checks non-mutating and warning-only when unobservable. Keep caption timeout at 90 seconds and stability at five seconds. Do not add fixed sleeps, duration comparison, trimming, playback-speed changes, provider retries, or SQLite access to `canva.py`.

- [ ] **Step 5: Preserve restartable workflow state**

Before each Canva attempt, quarantine any non-validated canonical final file. Keep checkpoint `FLOW_READY` until final validation. Map progress callbacks through `reporter.reached(job.id, milestone)`. A child/process failure must leave TTS audio and six Flow clips unchanged.

- [ ] **Step 6: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_storage.py -q`

Run: `.venv/bin/ruff check app/services/cloud_agent/providers/canva.py app/services/cloud_agent/workflow.py app/services/cloud_agent/storage.py test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_storage.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/cloud_agent/providers/canva.py app/services/cloud_agent/workflow.py app/services/cloud_agent/storage.py test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_storage.py
git commit -m "feat: expose restart-safe Canva progress"
```

---

### Task 7: Supervise Each Job in an Isolated Child Process

**Files:**
- Create: `app/services/cloud_agent/worker_process.py`
- Modify: `app/services/cloud_agent/worker.py`
- Create: `test/services/cloud_agent/test_worker_process.py`
- Modify: `test/services/cloud_agent/test_worker.py`

**Interfaces:**
- Consumes: progress signals, Job recovery fields, existing claim/lease/heartbeat behavior.
- Produces: `JobChildHandle`, `JobProcessLauncher`, `JobTerminationPort`, `MultiprocessingJobProcessLauncher`, `run_job_child()`, and supervisor deadline decisions.

- [ ] **Step 1: Write failing child lifecycle tests**

```python
def test_supervisor_claims_and_child_completes_job(fake_launcher, store):
    job = store.create_job(request())
    fake_launcher.complete(job.id)
    assert worker.run_once() is True
    assert store.get_job(job.id).status is CloudJobStatus.COMPLETED
    assert fake_launcher.started == [(job.id, worker.worker_id)]

def test_lease_is_owned_by_parent_while_child_runs(fake_launcher, store, clock):
    fake_launcher.block()
    worker.run_once_until(clock.advance(seconds=lease_renew_interval + 1))
    assert store.get_job(job.id).lease_until > clock.now_iso()
```

- [ ] **Step 2: Write failing termination/deadline tests**

```python
def test_canva_twenty_minute_idle_stops_old_child_before_restart(worker_fixture):
    worker, store, fake_launcher, termination_service, clock = worker_fixture
    seed_canva_job(last_progress_at=clock.minus(minutes=20))
    worker.run_once()
    assert fake_launcher.events[:3] == ["terminate", "confirmed_stopped", "start_attempt_2"]

def test_global_hour_idle_preempts_unused_canva_budget(worker_fixture):
    worker, store, fake_launcher, termination_service, clock = worker_fixture
    seed_canva_job(last_progress_at=clock.minus(minutes=60), canva_restarts=2)
    worker.run_once()
    assert fake_launcher.events == ["terminate", "confirmed_stopped"]
    assert termination_service.calls[0].reason_code == "JOB_STALLED_TIMEOUT"

def test_queued_job_wait_time_is_not_treated_as_active_stall(worker_fixture):
    worker, store, fake_launcher, termination_service, clock = worker_fixture
    queued = store.create_job(request())
    clock.advance(hours=2)
    claimed = worker.claim_for_test()
    assert claimed.last_progress_at == clock.now_iso()
```

- [ ] **Step 3: Run RED tests**

Run: `.venv/bin/pytest test/services/cloud_agent/test_worker_process.py test/services/cloud_agent/test_worker.py -q`

Expected: FAIL because the Worker still executes workflow inline.

- [ ] **Step 4: Implement a launcher abstraction and production child entry**

```python
@dataclass(frozen=True)
class ChildWaitResult:
    exited: bool
    exit_code: int | None
    progress_signal: ProgressSignal | None

class JobChildHandle(Protocol):
    def wait(self, timeout_seconds: float) -> ChildWaitResult:
        raise NotImplementedError
    def is_alive(self) -> bool:
        raise NotImplementedError
    def terminate_group(self, grace_seconds: float) -> bool:
        raise NotImplementedError

class JobProcessLauncher(Protocol):
    def start(self, job_id: str, worker_id: str) -> JobChildHandle:
        raise NotImplementedError

class JobTerminationPort(Protocol):
    def delete_stopped_job(
        self,
        job_id: str,
        *,
        child_stopped: bool,
        reason_code: str,
        stage: str,
    ) -> CloudJobIncident:
        raise NotImplementedError
```

Production uses a fresh multiprocessing context. `run_job_child(db_path, job_id, worker_id, signal_endpoint)` builds the workflow, browser, SQLite connection, and event dispatcher inside the child. Establish a distinct process group before browser launch. Never pass parent Playwright objects, workflow objects, threads, open SQLite connections, or dispatchers into the child.

- [ ] **Step 5: Convert Worker to an event/deadline supervisor**

After claim, initialize active progress, start the child, renew leases from the parent, and wait until the nearest lease renewal, Canva deadline, global deadline, progress signal, or child exit. On a progress signal, perform one durable Job read and recompute deadlines. Do not introduce WebUI polling or provider calls in the supervisor.

Canva deadline behavior:

```text
checkpoint FLOW_READY + active Canva status + 20m idle
-> terminate old child and confirm
-> reserve_canva_restart()
-> set attempt start without marking progress
-> start next child
```

Global deadline behavior delegates to the Task 8 termination service. If child exit is unexpected, retain the existing sanitized `WORKER_RUNTIME_ERROR` behavior unless a durable typed recovery result already exists.

After a normal child exit, reload the Job once. If its durable error code is
`FLOW_RECOVERY_EXHAUSTED` or `CANVA_RESTART_EXHAUSTED`, call
`JobTerminationPort.delete_stopped_job()` with `child_stopped=True`. Login,
CAPTCHA, 2FA, payment, security, unknown-modal, and other ordinary
`HUMAN_REQUIRED` results are not in this terminal-delete allowlist.

If `reserve_canva_restart()` raises `RecoveryBudgetExhausted`, write the durable
error code `CANVA_RESTART_EXHAUSTED`, confirm the child is stopped, and invoke
the same terminal-delete port. Do not start a fifth additional Canva attempt.

- [ ] **Step 6: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_worker_process.py test/services/cloud_agent/test_worker.py test/services/cloud_agent/test_workflow.py -q`

Run: `.venv/bin/ruff check app/services/cloud_agent/worker_process.py app/services/cloud_agent/worker.py test/services/cloud_agent/test_worker_process.py test/services/cloud_agent/test_worker.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/cloud_agent/worker_process.py app/services/cloud_agent/worker.py test/services/cloud_agent/test_worker_process.py test/services/cloud_agent/test_worker.py
git commit -m "feat: supervise cloud jobs in isolated processes"
```

---

### Task 8: Delete Terminally Stalled Jobs Locally and Notify the WebUI

**Files:**
- Modify: `app/services/cloud_agent/incidents.py`
- Modify: `app/services/cloud_agent/job_events.py`
- Modify: `app/services/cloud_agent/event_hub.py`
- Modify: `app/controllers/v1/cloud_agent.py`
- Modify: `webui/cloud_agent_events.py`
- Modify: `webui/cloud_agent.py`
- Modify: `test/services/cloud_agent/test_incidents.py`
- Modify: `test/services/cloud_agent/test_job_events.py`
- Modify: `test/services/cloud_agent/test_event_hub.py`
- Modify: `test/services/test_cloud_agent_controller.py`
- Modify: `test/services/test_cloud_agent_events.py`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**
- Consumes: confirmed-stopped child state, storage staging/purge, incident store, existing event dispatcher/SSE hub.
- Produces: `JobTerminationService.delete_stopped_job()`, `job.incident` event, unread/dismiss API routes, and isolated Thai incident banner.

- [ ] **Step 1: Write failing two-phase deletion tests**

```python
def test_termination_refuses_to_delete_while_child_is_alive(service, job):
    with pytest.raises(JobTerminationUnsafe):
        service.delete_stopped_job(job.id, child_stopped=False, reason_code="JOB_STALLED_TIMEOUT", stage="canva")
    assert store.get_job(job.id) is not None
    assert storage.prepare(job.id).job_dir.exists()

def test_successful_terminal_cleanup_deletes_local_job_but_keeps_incident(service, job):
    incident = service.delete_stopped_job(job.id, child_stopped=True, reason_code="FLOW_RECOVERY_EXHAUSTED", stage="google_flow")
    assert store.get_job(job.id) is None
    assert not storage._paths(job.id).job_dir.exists()
    assert incident_store.list_unread()[0].id == incident.id

def test_purge_failure_keeps_unclaimable_job_and_cleanup_incident(service_fixture):
    service, store, storage, incident_store, job = service_fixture
    storage.fail_purge = True
    incident = service.delete_stopped_job(
        job.id,
        child_stopped=True,
        reason_code="JOB_STALLED_TIMEOUT",
        stage="canva",
    )
    assert store.get_job(job.id).status is CloudJobStatus.HUMAN_REQUIRED
    assert incident.reason_code == "JOB_DELETE_CLEANUP_FAILED"
```

- [ ] **Step 2: Write failing event/API/WebUI tests**

```python
def test_incident_event_contains_no_subject_message_or_paths():
    payload = CloudJobIncidentEvent(
        event_id="event-1",
        type="job.incident",
        incident_id="incident-1",
        former_job_id="job-1",
        reason_code="JOB_STALLED_TIMEOUT",
        stage="canva",
        created_at="2026-08-28T00:00:00+00:00",
    ).model_dump(mode="json")
    assert set(payload) == {"event_id", "type", "incident_id", "former_job_id", "reason_code", "stage", "created_at"}

def test_incident_event_causes_one_unread_read_without_job_poll(monkeypatch):
    render_event({"type": "job.incident", "incident_id": "i-1"})
    assert api_calls == [("GET", "incidents?unread=true")]

def test_incident_banner_failure_does_not_hide_production_controls(monkeypatch):
    def fail_incidents(*_args, **_kwargs):
        raise RuntimeError("incident renderer unavailable")

    monkeypatch.setattr(ui, "render_incidents", fail_incidents)
    render_cloud_agent()
    assert controls_rendered is True
```

- [ ] **Step 3: Run RED tests**

Run: `.venv/bin/pytest test/services/cloud_agent/test_incidents.py test/services/cloud_agent/test_job_events.py test/services/cloud_agent/test_event_hub.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_events.py test/services/test_cloud_agent_webui.py -q`

- [ ] **Step 4: Implement safe terminal cleanup**

`delete_stopped_job()` must:

1. require `child_stopped=True`;
2. patch the Job to unclaimable `HUMAN_REQUIRED/delete_pending`;
3. create a pending sanitized incident before destructive local work;
4. stage and purge the Job directory through `CloudJobStorage`;
5. finalize the incident and delete the Job in one SQLite transaction;
6. publish the incident event only after the durable terminal result; and
7. never call Flow, Canva, TTS, Research, or remote deletion.

If staging/purge or final transaction fails, retain an unclaimable Job/tombstone, update the incident to `JOB_DELETE_CLEANUP_FAILED`, and do not claim deletion succeeded.

- [ ] **Step 5: Extend the safe event union and API**

Add `CloudJobIncidentEvent` with `type="job.incident"`. Update the internal intake and hub to accept `CloudJobEvent | CloudJobIncidentEvent`. Preserve bounded queues and `sync_required` overflow behavior.

Add:

```text
GET  /api/v1/cloud-agent/incidents?unread=true
POST /api/v1/cloud-agent/incidents/{incident_id}/dismiss
```

Responses expose the sanitized incident model only.

- [ ] **Step 6: Add isolated WebUI banner behavior**

Read unread incidents once on initial render and `sync_required`, and once for each unseen `job.incident`. If the selected Job matches `former_job_id`, clear that selection. Render Thai stage/reason/attempt/time copy plus Dismiss. Wrap incident fetch/render errors so Production status and controls remain usable. Do not add `st.fragment(run_every=2)` or another timer.

- [ ] **Step 7: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/cloud_agent/test_incidents.py test/services/cloud_agent/test_job_events.py test/services/cloud_agent/test_event_hub.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_events.py test/services/test_cloud_agent_webui.py -q`

Run: `.venv/bin/ruff check app/services/cloud_agent/incidents.py app/services/cloud_agent/job_events.py app/services/cloud_agent/event_hub.py app/controllers/v1/cloud_agent.py webui/cloud_agent_events.py webui/cloud_agent.py test/services/cloud_agent/test_incidents.py test/services/cloud_agent/test_job_events.py test/services/cloud_agent/test_event_hub.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_events.py test/services/test_cloud_agent_webui.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/cloud_agent/incidents.py app/services/cloud_agent/job_events.py app/services/cloud_agent/event_hub.py app/controllers/v1/cloud_agent.py webui/cloud_agent_events.py webui/cloud_agent.py test/services/cloud_agent/test_incidents.py test/services/cloud_agent/test_job_events.py test/services/cloud_agent/test_event_hub.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_events.py test/services/test_cloud_agent_webui.py
git commit -m "feat: notify and remove terminally stalled cloud jobs"
```

---

### Task 9: Wire Guardrails, Composition, and Deployment Contracts

**Files:**
- Modify: `app/config/config.py`
- Modify: `config.example.toml`
- Modify: `app/services/cloud_agent/factory.py`
- Modify: `test/services/test_config.py`
- Modify: `test/services/cloud_agent/test_worker.py`
- Modify: `test/services/test_cloud_agent_deploy.py`

**Interfaces:**
- Consumes: recovery coordinator, progress reporter, child launcher, incident/termination services.
- Produces: validated configuration and production composition with exact defaults.

- [ ] **Step 1: Write failing config and composition tests**

```python
def test_cloud_recovery_guardrail_defaults_are_exact():
    assert config.app["cloud_agent_flow_recovery_retries"] == 2
    assert config.app["cloud_agent_canva_restart_retries"] == 4
    assert config.app["cloud_agent_canva_stall_seconds"] == 1200
    assert config.app["cloud_agent_job_stall_seconds"] == 3600

def test_factory_builds_parent_supervisor_without_parent_browser(monkeypatch):
    worker = factory.build_worker()
    assert worker.process_launcher is not None
    assert not hasattr(worker, "browser")
    assert browser_builds_in_parent == 0
```

- [ ] **Step 2: Run RED tests**

Run: `.venv/bin/pytest test/services/test_config.py test/services/cloud_agent/test_worker.py test/services/test_cloud_agent_deploy.py -q`

- [ ] **Step 3: Add exact bounded settings**

Add defaults and example values:

```toml
cloud_agent_flow_recovery_retries = 2
cloud_agent_canva_restart_retries = 4
cloud_agent_canva_stall_seconds = 1200
cloud_agent_job_stall_seconds = 3600
cloud_agent_child_terminate_grace_seconds = 15
cloud_agent_progress_signal_queue_size = 64
```

Validate retries as non-negative integers capped at the product values, Canva stall below global stall, positive termination grace, and positive bounded queue size. These settings are not editable through Custom Prompt or WebUI.

- [ ] **Step 4: Compose parent and child separately**

`build_worker()` creates only parent-safe store, event/incident services, process launcher, clock, and supervisor. `build_job_child()` creates browser/session/provider/workflow objects inside the child. Preserve current event dispatcher non-blocking behavior and worker queue poll setting.

- [ ] **Step 5: Run focused tests and lint**

Run: `.venv/bin/pytest test/services/test_config.py test/services/cloud_agent/test_worker.py test/services/test_cloud_agent_deploy.py -q`

Run: `.venv/bin/ruff check app/config/config.py app/services/cloud_agent/factory.py test/services/test_config.py test/services/cloud_agent/test_worker.py test/services/test_cloud_agent_deploy.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/config/config.py config.example.toml app/services/cloud_agent/factory.py test/services/test_config.py test/services/cloud_agent/test_worker.py test/services/test_cloud_agent_deploy.py
git commit -m "feat: configure cloud recovery supervision"
```

---

### Task 10: Full Regression, Review, and Controlled Deployment Gate

**Files:**
- Modify only if tests expose an in-scope regression: files already listed in Tasks 1-9.
- Review: `docs/superpowers/specs/2026-08-28-cloud-agent-recovery-watchdog-design.md`
- Review: `docs/superpowers/plans/2026-08-28-cloud-agent-recovery-watchdog.md`

**Interfaces:**
- Consumes: all Task 1-9 deliverables and rollback checkpoint.
- Produces: verified implementation ready for explicit merge/push/deployment approval.

- [ ] **Step 1: Run the complete Cloud Agent suite**

Run:

```bash
.venv/bin/pytest \
  test/services/cloud_agent \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_events.py \
  test/services/test_cloud_agent_ui.py \
  test/services/test_cloud_agent_video_library.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_completed_videos_page.py \
  test/services/test_cloud_agent_deploy.py -q
```

Expected: all PASS, no paid-provider/browser calls.

- [ ] **Step 2: Run full repository regression and lint**

Run: `.venv/bin/pytest -q`

Expected: all PASS.

Run: `.venv/bin/ruff check app test webui`

Expected: PASS.

- [ ] **Step 3: Run spec-coverage assertions**

Run:

```bash
rg -n "run_every\s*=\s*2|LIVE_JOB_REFRESH_SECONDS" webui test
rg -n "FLOW_RECOVERY_EXHAUSTED|CANVA_RESTART_EXHAUSTED|JOB_STALLED_TIMEOUT|JOB_DELETE_CLEANUP_FAILED" app test
rg -n "cloud_agent_flow_recovery_retries|cloud_agent_canva_restart_retries|cloud_agent_canva_stall_seconds|cloud_agent_job_stall_seconds" app config.example.toml test
```

Expected: the first command finds only explicit negative regression assertions or no matches; the other commands find implementation plus focused tests for every contract.

- [ ] **Step 4: Request two-stage code review**

First review checks exact spec compliance, paid-operation fences, retry budgets, deletion ordering, and no remote deletion. Second review checks process lifecycle, SQLite concurrency, security projection, failure isolation, and regression quality. Fix only evidence-backed findings and rerun the focused and full gates after each fix commit.

- [ ] **Step 5: Verify rollback checkpoint before any deployment**

Run:

```bash
git rev-list -n 1 rollback/cloud-agent-before-recovery-20260828
cd storage/rollback-checkpoints/20260828T140304Z-8fe7898 && sha256sum -c SHA256SUMS
```

Expected: tag resolves to `8fe78986f637edd23521778046608c4b516e64e5`; every checkpoint file reports `OK`.

- [ ] **Step 6: Commit final in-scope regression fixes, if any**

```bash
git status --short
```

If Step 1-5 required a source fix, return to its owning Task, rerun that Task's
focused tests, and use that Task's exact `git add` command before committing
`test: verify cloud recovery watchdog`. Skip this commit when Step 1-5 require
no source changes.

- [ ] **Step 7: Stop for explicit integration/deployment approval**

Report branch, commits, focused/full test counts, Ruff result, rollback tag/hash, checkpoint verification, and remaining real-provider smoke risks. Do not merge, push, restart services, or run paid Google Flow/Canva smoke tests without the user's explicit approval.
