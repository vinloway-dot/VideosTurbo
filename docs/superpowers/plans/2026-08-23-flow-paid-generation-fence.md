# Durable Flow Paid-Generation Fence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent any automatic duplicate Google Flow generation after a crash between the paid Generate click and durable `FLOW_READY`, while safely reconciling existing remote results and containing expected Flow UI failures inside one job.

**Architecture:** Add a server-owned per-job SQLite boolean that is atomically set after stable workspace preparation but before the remote Generate click. Split the provider into fresh preparation, paid generation, and no-generate reconciliation operations so `CloudAgentWorkflow` can own all durable transitions without giving the provider database access. Gate workspace inspection on the live-proven settled editor/media-inventory barrier and map typed post-fence failures to durable `HUMAN_REQUIRED` state instead of terminating the Worker.

**Tech Stack:** Python 3.11+, Pydantic, stdlib `sqlite3`, Playwright sync API, existing persistent `ProfileLock`, existing Flow ZIP/materialization validation, pytest, Ruff, systemd, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md` — Design Spec v2.4, durable paid Google Flow generation fence, commit `8050364`.

## Global Constraints

- Keep `videosturbo-worker.service` stopped throughout implementation, migration, live remediation, push, and CI.
- Do not click Flow Generate, delete or pre-clean the current Flow workspace, synthesize TTS, touch the Canva profile, or start Task 15.
- Do not delete or alter the current two remote Saturn assets.
- Write and observe each focused RED test before changing the corresponding production behavior.
- `CloudAgentWorkflow` owns durable state transitions; `CloudJobStore` owns schema/migration/persistence; Google Flow providers never write SQLite.
- A true `flow_generation_unresolved` fence forbids automatic pre-clean, remote deletion, and Generate.
- Local recovery priority remains canonical, surviving ZIP, then staging, including when the generation fence is true.
- Use only live-proven observable Flow state: actionable Agent/composer, All media control, visible `virtuoso-item-list`, absence of visible busy/progress state, and stable inventory over at least three polls.
- Do not use coordinate selectors or transient zero checkbox/card/text state as empty proof.
- Editor readiness defaults to 120 seconds and remains separate from the 1,800-second generation/reconciliation wait.
- `FLOW_READY` atomically writes `flow_generation_unresolved=false` and `flow_cleanup_unresolved=true` before remote cleanup.
- Expected typed Flow failures must become durable job state and must not normally escape the workflow/kill the Worker.
- No global workspace-state table, second config loader, FFmpeg assembly fallback, public network change, credential logging, profile copy, or profile deletion.

---

## Repository Review and File Boundaries

The v2.4 design was reviewed against commit `c39edb2`:

- `app/models/cloud_agent.py` exposes only the existing cleanup flag.
- `app/services/cloud_agent/job_store.py` has a compatible additive migration map and atomic single-row `patch_job()` that can carry the new field.
- `app/services/cloud_agent/providers/google_flow.py::FlowWorkspaceRun.generate_and_download()` currently pre-cleans and clicks Generate in one call, preventing a workflow-owned durable write between those operations.
- `GoogleFlowClient._enter_project_editor()` uses `generation_timeout_seconds`, and `preclean_and_verify_empty()` accepts zero checkboxes plus one transient empty-state text.
- `app/services/cloud_agent/workflow.py` has no reconciliation branch for unresolved paid generation and catches only narration-policy and human-auth errors.
- `app/services/cloud_agent/worker.py` lets uncaught provider exceptions terminate the process; containing the typed error in workflow should require no broad Worker exception swallowing.
- The live job remains `FLOW_GENERATING/TTS_READY` with one preserved canonical audio artifact; the Worker is intentionally stopped.

Files remain responsibility-focused:

- `app/models/cloud_agent.py`: API/server record shape.
- `app/services/cloud_agent/job_store.py`: compatible SQLite field and atomic updates.
- `app/services/cloud_agent/providers/google_flow.py`: settled UI observation and remote fresh/reconciliation actions.
- `app/services/cloud_agent/workflow.py`: fence ordering, recovery choice, checkpoints, and typed failure mapping.
- `test/services/cloud_agent/test_job_store.py`: migration and durable atomic state.
- `test/services/cloud_agent/test_google_flow.py`: provider action/readiness behavior with no external account.
- `test/services/cloud_agent/test_workflow.py`: paid fence, crash/restart, reconciliation, cleanup, and error boundaries.
- `test/services/cloud_agent/test_worker.py`: one expected Flow failure does not escape `run_once()`.

---

## Task 1: Durable generation-fence field and compatible migration

**Files:**

- Modify: `app/models/cloud_agent.py`
- Modify: `app/services/cloud_agent/job_store.py`
- Modify: `test/services/cloud_agent/test_job_store.py`

**Interfaces:**

- Produces: `CloudJobRecord.flow_generation_unresolved: bool = False`.
- Produces: SQLite `flow_generation_unresolved INTEGER NOT NULL DEFAULT 0`.
- Produces: support for `flow_generation_unresolved` in `CloudJobStore.patch_job()`.

- [ ] **Step 1: RED — migration, default, and round trip**

Extend `test_pre_v22_database_is_migrated_without_losing_job()` and add a new-store assertion:

```python
assert migrated.flow_generation_unresolved is False
assert "flow_generation_unresolved" in columns

created = store.create_job(_request())
assert created.flow_generation_unresolved is False
assert CloudJobStore(str(db_path)).get_job(created.id).flow_generation_unresolved is False
```

The production mutation caught is an absent/additively unmigrated fence column.

- [ ] **Step 2: Run migration RED**

Run:

```bash
uv run pytest test/services/cloud_agent/test_job_store.py -k "migration or generation_unresolved" -v
```

Expected RED: `CloudJobRecord` has no `flow_generation_unresolved` attribute and the migrated table lacks the column.

- [ ] **Step 3: GREEN — add the record field and compatible column**

Add the Pydantic field only to `CloudJobRecord`, then extend `_COMPATIBLE_COLUMNS`, `_MUTABLE_COLUMNS`, table creation, insert columns/values, and `_row_to_record()` using `bool(row["flow_generation_unresolved"])`. Do not expose the field through `CloudJobCreate`.

- [ ] **Step 4: RED — atomic fence and FLOW_READY commit**

Add two tests using a reopened `CloudJobStore`:

```python
fenced = store.patch_job(
    job.id,
    status=CloudJobStatus.FLOW_GENERATING,
    checkpoint=CloudJobCheckpoint.TTS_READY,
    current_step="flow_generating",
    progress=35,
    flow_generation_unresolved=True,
)
assert fenced.flow_generation_unresolved is True

ready = store.patch_job(
    job.id,
    status=CloudJobStatus.FLOW_READY,
    checkpoint=CloudJobCheckpoint.FLOW_READY,
    current_step="flow_ready",
    progress=60,
    flow_generation_unresolved=False,
    flow_cleanup_unresolved=True,
)
```

Reload and assert every literal state value. The production mutations caught are a missing fence write and a non-atomic/partially updated `FLOW_READY` transition.

- [ ] **Step 5: Run atomic-state RED, then minimal GREEN**

Run the two named tests, add no helper beyond existing `patch_job()`, and rerun until both pass.

- [ ] **Step 6: Verify Task 1**

```bash
uv run pytest test/services/cloud_agent/test_job_store.py -v
uv run ruff check app/models/cloud_agent.py app/services/cloud_agent/job_store.py test/services/cloud_agent/test_job_store.py
git diff --check
```

---

## Task 2: Settled editor and stable inventory gate

**Files:**

- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`

**Interfaces:**

- Extend `GoogleFlowClient.__init__(..., editor_ready_timeout_seconds: float = 120.0, settled_poll_count: int = 3)`.
- Produce `GoogleFlowClient._wait_for_settled_editor(page: Any) -> None`.
- Produce `GoogleFlowClient._wait_for_stable_inventory(page: Any, *, expected_count: int | None) -> int`.
- Produce `FlowWorkspaceRun.prepare_for_generation() -> None`.
- Change `FlowWorkspaceRun.generate_and_download()` to begin at the paid submit action; it no longer pre-cleans.

- [ ] **Step 1: RED — editor timeout is independent from generation timeout**

Update the fake page/locator to expose document ready state, unique visible/enabled Agent+composer, All media, one visible `virtuoso-item-list`, busy/progress counts, and inventory sequences. Add:

```python
client, _ = _client(page, timeout_seconds=1800.0, editor_ready_timeout_seconds=1.0)
with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
    with client.acquire_workspace(_job()):
        pass
```

Drive `time.monotonic()` with values crossing 1.0 but not 1,800. The production mutation caught is reusing the paid-generation timeout for editor readiness.

- [ ] **Step 2: Run editor-timeout RED**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -k "editor_timeout_is_independent" -v
```

Expected RED: constructor rejects the new argument or the existing loop still uses the 1,800-second deadline.

- [ ] **Step 3: GREEN — separate bounded editor readiness**

Validate positive timeout/poll values. Require all live-observed conditions together: complete document, exact visible/enabled Agent, unique visible composer, unique All media control, unique visible `virtuoso-item-list`, and no visible `aria-busy=true` or progressbar. Do not click Generate or mutate assets.

- [ ] **Step 4: RED — transient zero inventory cannot pass**

Add a fake sequence that reports a loading/unstable inventory such as `[0, 0, 2]` and assert:

```python
with client.acquire_workspace(_job()) as workspace:
    with pytest.raises(FlowWorkspaceVerificationError, match="empty product workspace"):
        workspace.prepare_for_generation()
assert ("click", "generate") not in page.actions
```

Also assert one empty-state text/zero checkbox observation cannot make the test pass. The production mutation caught is the exact paid-smoke race.

- [ ] **Step 5: Run transient-empty RED**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -k "transient_empty or loading_inventory" -v
```

Expected RED: existing `preclean_and_verify_empty()` returns after the transient empty marker.

- [ ] **Step 6: GREEN — stable observable inventory**

Use the observed visible `[data-testid="virtuoso-item-list"]` boundary and its observable product-card descendants (`[role="button"][tabindex="0"]`). Reset stability on any readiness/busy/count change. Return only after the same count appears for `settled_poll_count` consecutive polls; for empty verification require literal count `0`.

- [ ] **Step 7: RED/GREEN — stable settled empty may pass**

Add inventory `[0, 0, 0]` with the entire actionable barrier true; assert `prepare_for_generation()` returns, reload happened, and no Generate click occurred. Then run:

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -k "settled_empty or transient_empty or editor_timeout" -v
```

- [ ] **Step 8: RED/GREEN — split preparation from paid submit**

Change the existing generation test to call:

```python
workspace.prepare_for_generation()
workspace.generate_and_download(_job(), paths)
```

Assert all delete/reload/stable-empty actions precede the single Generate click. Add a separate call to `generate_and_download()` without preparation and assert the provider does not silently pre-clean. Implement only the method split; no database access enters the provider.

- [ ] **Step 9: Verify Task 2**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -v
uv run ruff check app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py
git diff --check
```

---

## Task 3: No-generate remote reconciliation provider path

**Files:**

- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`

**Interfaces:**

- Produce `FlowWorkspaceRun.reconcile_and_download(job: CloudJobRecord, paths: JobPaths, expected_count: int = 6) -> tuple[Path, ...]`.
- Reuse one private `_rename_download_and_materialize(...)` helper after generation/reconciliation completion.

- [ ] **Step 1: RED — six existing results reconcile without Generate**

Create a fake page whose current request is observably `6 / 6`, whose rename completes, and whose bulk ZIP fixture materializes six videos. Call only `reconcile_and_download()` and assert:

```python
assert ("click", "generate") not in paid_prompt_actions
assert not any(action[0] in {"check", "delete"} for action in page.actions)
assert [path.name for path in result] == [f"clip_{n:02d}.mp4" for n in range(1, 7)]
```

The production mutation caught is routing unresolved recovery through fresh pre-clean/Generate.

- [ ] **Step 2: Run reconciliation RED**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -k "reconcile_existing_six" -v
```

Expected RED: `FlowWorkspaceRun` has no reconciliation method.

- [ ] **Step 3: GREEN — reuse completion/rename/download only**

`reconcile_and_download()` waits for the already-submitted request, then reuses semantic rename, refresh, name verification, bulk ZIP, and archive materialization. It never calls `prepare_for_generation()`, `_submit_generation()`, or cleanup.

- [ ] **Step 4: RED/GREEN — partial results are retained**

Drive the bounded existing-request wait to `FlowWorkspaceVerificationError` before 6/6. Assert no check/delete/Generate/rename/download action occurred. Implement a typed, sanitized reconciliation failure without mutating the remote workspace.

- [ ] **Step 5: Verify Task 3**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -k "reconcile" -v
uv run pytest test/services/cloud_agent/test_google_flow.py -v
uv run ruff check app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py
```

---

## Task 4: Workflow-owned paid fence, crash recovery, and error containment

**Files:**

- Modify: `app/services/cloud_agent/workflow.py`
- Modify: `test/services/cloud_agent/test_workflow.py`
- Modify: `test/services/cloud_agent/test_worker.py`

**Interfaces:**

- Extend `FlowWorkspace` protocol with `prepare_for_generation()` and `reconcile_and_download(...)`.
- Produce error code literal `FLOW_GENERATION_RECONCILIATION_REQUIRED` at the workflow boundary.
- `FLOW_READY` transition writes both unresolved booleans in one store call.

- [ ] **Step 1: RED — fence is persisted before paid submit**

Extend `RecordingWorkspace` with separate `prepare`, `generate`, and `reconcile` events. In `generate_and_download()`, reload the real store and record the fence value. Assert event order:

```python
assert events[:4] == [
    "workspace_enter",
    "prepare",
    ("generate", CloudJobCheckpoint.TTS_READY, True),
    ("flow_ready", False, True),
]
```

The production mutation caught is clicking Generate before the durable write.

- [ ] **Step 2: Run fence-order RED**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -k "generation_fence_precedes_submit" -v
```

Expected RED: the record has no fence and the current workspace generates directly.

- [ ] **Step 3: GREEN — fresh flow path**

After local salvage fails, acquire the workspace. If the fence is false, call `prepare_for_generation()`, then atomically patch `FLOW_GENERATING/TTS_READY/flow_generation_unresolved=true`, reload the job, and call `generate_and_download()`. Validate all six local files as before.

- [ ] **Step 4: RED — crash immediately before and after click never generates twice**

Add two deterministic workspace failures:

1. Raise after the workflow fence write but before recording a Generate click.
2. Record one Generate click, then raise immediately.

Run `workflow.run()` twice on the same store/job. Assert the second run calls `reconcile_and_download()` and total Generate calls remain respectively `0` and `1`; both jobs retain `flow_generation_unresolved=true` unless reconciliation succeeds.

- [ ] **Step 5: Run crash-window RED, then GREEN reconciliation branch**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -k "crash_before_generate or crash_after_generate" -v
```

When `TTS_READY + flow_generation_unresolved=true` and local recovery is unavailable, call only `reconcile_and_download()`. Never call `prepare_for_generation()` in this branch.

- [ ] **Step 6: RED — typed failures before/after fence map differently**

Add focused tests:

```python
# preparation error before fence
assert result.status is CloudJobStatus.FAILED
assert result.checkpoint is CloudJobCheckpoint.TTS_READY
assert result.flow_generation_unresolved is False

# workspace/archive error after fence
assert result.status is CloudJobStatus.HUMAN_REQUIRED
assert result.checkpoint is CloudJobCheckpoint.TTS_READY
assert result.flow_generation_unresolved is True
assert result.error_code == "FLOW_GENERATION_RECONCILIATION_REQUIRED"
```

Assert partial remote results cause no cleanup/generation action.

- [ ] **Step 7: GREEN — contain expected Flow errors**

Catch `FlowWorkspaceVerificationError` and `FlowArchiveValidationError` at the workflow boundary. Reload the current record before deciding: true fence maps to reconciliation-required `HUMAN_REQUIRED`; false fence maps to a durable pre-paid `FAILED` state with a sanitized Flow workspace/archive error code. Do not catch arbitrary programming errors.

- [ ] **Step 8: RED/GREEN — atomic FLOW_READY clears generation fence**

Update the existing cleanup-order test to inspect one store snapshot at cleanup entry and assert:

```python
checkpoint is FLOW_READY
flow_generation_unresolved is False
flow_cleanup_unresolved is True
```

Keep the existing cleanup-success/failure semantics and pause/cancel ordering.

- [ ] **Step 9: RED/GREEN — local salvage and FLOW_READY resume never generate**

Run the existing canonical/ZIP/staging recovery tests with the generation fence true and assert they still recover to `FLOW_READY` with zero Generate calls. Update the existing `FLOW_READY` resume test to start with either fence value and assert no Flow workspace is opened.

- [ ] **Step 10: RED/GREEN — expected provider failure does not terminate Worker**

Build a real `CloudAgentWorkflow` with a preparation fake raising `FlowWorkspaceVerificationError`, then call `CloudAgentWorker.run_once()`. Assert it returns `True`, releases the lease, leaves the durable job terminal/HUMAN_REQUIRED as appropriate, and a second `run_once()` does not reclaim it. No broad try/except is added to `CloudAgentWorker` unless this real boundary test still proves one necessary.

- [ ] **Step 11: Verify Task 4**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_worker.py -v
uv run ruff check app/services/cloud_agent/workflow.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_worker.py
git diff --check
```

---

## Task 5: Full automated regression and live-job remediation with Worker stopped

**Files:**

- Modify only if a proven regression requires it: `app/services/cloud_agent/factory.py`
- Runtime migration only: `storage/cloud-agent.sqlite3`
- No media/profile file changes.

**Interfaces:**

- Production `CloudJobStore` additive initialization installs the new column.
- One explicit store patch remediates job `c604f5d5-c206-4d49-bad2-cac59e2815a2`.

- [ ] **Step 1: Focused verification**

```bash
uv run pytest \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_google_flow.py \
  test/services/cloud_agent/test_workflow.py \
  test/services/cloud_agent/test_worker.py \
  -v
```

- [ ] **Step 2: Full Cloud Agent regression**

```bash
uv run pytest test/services/cloud_agent test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py -v
```

- [ ] **Step 3: Full repository suite and Ruff**

```bash
uv run pytest -v
uv run ruff check \
  app/models/cloud_agent.py \
  app/services/cloud_agent/job_store.py \
  app/services/cloud_agent/providers/google_flow.py \
  app/services/cloud_agent/workflow.py \
  app/services/cloud_agent/worker.py \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_google_flow.py \
  test/services/cloud_agent/test_workflow.py \
  test/services/cloud_agent/test_worker.py
git diff --check
```

- [ ] **Step 4: Install compatible schema without starting Worker**

First prove `systemctl is-active videosturbo-worker.service` returns `inactive`. Instantiate `CloudJobStore` against the configured database to run only the additive migration. Query `PRAGMA table_info(cloud_agent_jobs)` and confirm the non-null/default-zero generation-fence column. Do not open a browser.

- [ ] **Step 5: Remediate the current live job once**

Read the job and assert its checkpoint is `TTS_READY`, canonical `voice.mp3` exists, duration is exactly `63.936`, playback is `0.9384384384384384`, and no six canonical Flow clips exist. Then use one `patch_job()` call:

```python
store.patch_job(
    job.id,
    status=CloudJobStatus.HUMAN_REQUIRED,
    checkpoint=CloudJobCheckpoint.TTS_READY,
    current_step="human_required",
    flow_generation_unresolved=True,
    error_code="FLOW_GENERATION_RECONCILIATION_REQUIRED",
    error_message="Existing paid Flow generation requires remote reconciliation.",
)
```

Reload and verify every field while the Worker remains inactive. Verify the canonical audio checksum/size/duration is unchanged, no Chrome owns the Flow profile, and no local or remote cleanup command was run.

- [ ] **Step 6: Commit and push**

```bash
git status --short
git diff --check
git add app test
git commit -m "feat: fence paid flow generation"
git push origin feature/cloud-video-agent
```

Do not commit SQLite, audio, screenshots, browser profiles, locks, or media.

- [ ] **Step 7: CI and final safety gate**

Wait for Windows, Python 3.11, and Python 3.13 checks. On failure, inspect the exact job/log and use one RED→GREEN correction. After CI, verify:

```text
Worker inactive
API/WebUI active
Xvfb/Openbox/x11vnc/noVNC unchanged
127.0.0.1:5900 and 127.0.0.1:6080 loopback-only
current job HUMAN_REQUIRED/TTS_READY
flow_generation_unresolved=true
voice.mp3 preserved
remote assets preserved
PAID_GATE=BLOCKED_PENDING_EXPLICIT_RECONCILIATION_AUTHORIZATION
```

Do not restart the Worker or resume Task 14 paid work in this plan.
