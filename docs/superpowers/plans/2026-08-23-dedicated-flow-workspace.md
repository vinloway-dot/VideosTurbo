# Dedicated Google Flow Workspace Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Before every production-code behavior change, use `superpowers:test-driven-development`; on any unexpected failure, use `superpowers:systematic-debugging`; before any completion claim or commit, use `superpowers:verification-before-completion`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace locator-order, per-button Google Flow downloads with one exclusively locked, self-cleaning Flow workspace that bulk-downloads and semantically orders six clips, durably checkpoints before remote cleanup, and recovers canonical, ZIP, or staged local artifacts after crashes without repeating paid generation.

**Architecture:** The existing cross-process `google_flow` persistent-profile lock becomes the shared-project workspace lock. `GoogleFlowClient.acquire_workspace()` owns one browser context and lock across pre-clean, observable empty verification, generation, Agent rename, bulk ZIP download, and post-checkpoint cleanup. A new local archive/materialization component securely validates and stages all six semantic clips before exposing canonical paths. `CloudAgentWorkflow` owns recovery priority and checkpoint/control transitions; `CloudJobStore` owns a compatible SQLite migration and atomic job update; providers never write SQLite.

**Tech Stack:** Python >=3.11, Pydantic, stdlib `sqlite3`, `zipfile`, `pathlib`, `os.replace`, Playwright sync API, existing `ProfileLock`, FFmpeg/ffprobe media validation, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md` — Design Spec v2.3, Adaptive Six-Clip + dedicated shared Google Flow workspace, committed as `167c9cb`.

## Global Constraints

- No production behavior is written before its focused RED test has failed for the intended missing behavior.
- Do not consume Google Flow or production TTS credits while implementing or running automated tests.
- Use the one configured Flow project; never create one project per job.
- Hold the `google_flow` profile/workspace lock across the whole remote transaction, including durable `FLOW_READY` persistence and attempted cleanup.
- Session check/repair endpoints keep their current short bounded lock timeout. Only production workspace acquisition receives the long-wait override.
- Every new Flow owner pre-cleans and observably verifies zero product clips before paid generation, regardless of any prior job flag.
- Do not infer semantic clip order from generation, completion, DOM, locator, ZIP member, or download order.
- Send the exact proven Agent rename instruction: `เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ`.
- Require exactly one semantic `clip 1` through `clip 6` after refresh and exactly six corresponding valid MP4 files in the ZIP/staging set.
- Protect archive extraction against absolute paths, `..`, backslash traversal, drive-qualified paths, symlinks, encrypted entries, duplicate/ambiguous semantic numbers, and incomplete or extra video sets.
- Do not expose canonical `clip_01.mp4` through `clip_06.mp4` until all six staged files have passed existing media validation.
- Recovery order is canonical, then surviving ZIP, then surviving semantic staging. Partial/invalid canonical artifacts are quarantined before salvage; salvageable archive/staging data is not deleted first.
- Persist `checkpoint=FLOW_READY` and `flow_cleanup_unresolved=true` together in one SQLite update before remote cleanup.
- Set `flow_cleanup_unresolved=false` only after cleanup, refresh, and observable zero-product-clip verification.
- Post-`FLOW_READY` cleanup failure or interruption must preserve local artifacts and the checkpoint, must not regenerate, and must not fail the job.
- Attempt post-`FLOW_READY` cleanup before honoring a newly arrived pause/cancel request.
- Resume at `FLOW_READY` validates canonical clips and proceeds to Canva without opening Flow or regenerating.
- Do not introduce a global workspace-state table. Do not let `GoogleFlowClient` write SQLite.
- Do not alter Canva assembly, TTS routing, legacy rendering/stock paths, public network exposure, or authenticated browser profiles.
- Never log URLs with signed media data, cookies, tokens, credentials, profile contents, or archive download URLs.

---

## Repository Review and Delta

The v2.3 design was checked against the current `feature/cloud-video-agent` implementation before this plan was written:

- `app/services/cloud_agent/providers/google_flow.py::GoogleFlowClient.generate_and_download()` opens and releases the profile within one provider call, enumerates individual Download locators, and maps locator order directly to canonical filenames.
- `app/services/cloud_agent/workflow.py::FlowClient` has no context-managed workspace boundary. The workflow persists `FLOW_READY` only after the provider returns, so the browser/profile lock cannot remain held through the checkpoint and cleanup attempt.
- `app/services/cloud_agent/browser.py::PersistentBrowserManager.open()` always uses the manager's short `lock_timeout_seconds`; session checks and production work cannot currently choose different wait policies.
- `app/models/cloud_agent.py::CloudJobRecord` and `app/services/cloud_agent/job_store.py` have no `flow_cleanup_unresolved` field or migration.
- `app/services/cloud_agent/storage.py::JobPaths` has canonical Flow files only; it has no job-local archive, staging, or quarantine paths.
- `app/services/cloud_agent/errors.py` has no typed Flow workspace/archive validation errors.
- Existing workflow tests use a flat `RecordingFlow.generate_and_download()` fake; they must move to a context-managed fake workspace without weakening existing TTS/Canva/checkpoint assertions.

This plan changes only those boundaries.

---

## Task 1: Durable unresolved-cleanup state and compatible SQLite migration

**Files:**

- Modify: `app/models/cloud_agent.py`
- Modify: `app/services/cloud_agent/job_store.py`
- Modify: `test/services/cloud_agent/test_models.py`
- Modify: `test/services/cloud_agent/test_job_store.py`

**Interface produced:**

```python
class CloudJobRecord(CloudJobCreate):
    flow_cleanup_unresolved: bool = False
```

SQLite column:

```sql
flow_cleanup_unresolved INTEGER NOT NULL DEFAULT 0
```

- [ ] **Step 1: RED — record default and serialization**

Add a model test asserting a current record defaults to `False` and accepts an explicit `True`. Do not add the field to `CloudJobCreate`; clients cannot claim cleanup success or failure.

- [ ] **Step 2: Run model RED**

```bash
uv run pytest test/services/cloud_agent/test_models.py -k "flow_cleanup_unresolved" -v
```

Expected RED: `CloudJobRecord` has no `flow_cleanup_unresolved` field.

- [ ] **Step 3: GREEN — add only the record field**

Add the server-owned boolean with a restart-safe default of `False`.

- [ ] **Step 4: RED — migration, round trip, and atomic checkpoint update**

Add store tests that:

1. Create a pre-v2.3 database schema without the column, open it with `CloudJobStore`, and assert its existing row loads with `flow_cleanup_unresolved is False`.
2. Create a new job and assert the column round-trips as `False`.
3. Perform one call:

```python
updated = store.patch_job(
    job.id,
    status=CloudJobStatus.FLOW_READY,
    checkpoint=CloudJobCheckpoint.FLOW_READY,
    current_step="flow_ready",
    progress=60,
    flow_cleanup_unresolved=True,
)
```

Then reload through a fresh store connection and assert all five values are durable together.

4. Patch only `flow_cleanup_unresolved=False` and assert the checkpoint remains `FLOW_READY`.

- [ ] **Step 5: Run store RED**

```bash
uv run pytest test/services/cloud_agent/test_job_store.py -k "flow_cleanup or migration" -v
```

Expected RED: the SQLite column and mutable mapping do not exist.

- [ ] **Step 6: GREEN — compatible migration and mapping**

Extend the existing additive-column migration, insert values, `_row_to_record()`, and `_MUTABLE_COLUMNS`. Convert SQLite integer values with `bool(row["flow_cleanup_unresolved"])`. Do not recreate the jobs table and do not add a global workspace-state table.

- [ ] **Step 7: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py -v
uv run ruff check app/models/cloud_agent.py app/services/cloud_agent/job_store.py test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py
git diff --check
git add app/models/cloud_agent.py app/services/cloud_agent/job_store.py test/services/cloud_agent/test_models.py test/services/cloud_agent/test_job_store.py
git commit -m "feat: persist unresolved flow cleanup"
```

---

## Task 2: Job-local archive, staging, and quarantine boundaries

**Files:**

- Modify: `app/services/cloud_agent/storage.py`
- Modify: `app/services/cloud_agent/errors.py`
- Modify: `test/services/cloud_agent/test_storage.py`

**Interfaces produced:**

```python
@dataclass(frozen=True)
class JobPaths:
    flow_downloads_dir: Path
    flow_staging_dir: Path
    flow_quarantine_dir: Path
    flow_archive_file: Path

class CloudJobStorage:
    def quarantine_flow_canonical(self, job_id: str) -> Path | None: ...

class FlowWorkspaceVerificationError(MediaValidationError): ...
class FlowArchiveValidationError(MediaValidationError): ...
```

Canonical and temporary layout:

```text
storage/jobs/<job_id>/flow/
├── clip_01.mp4 ... clip_06.mp4
├── downloads/product_clips.zip
├── staging/
└── quarantine/<unique-recovery-id>/
```

- [ ] **Step 1: RED — deterministic paths and preparation**

Add tests asserting all new paths remain under the validated job directory, `prepare()` creates the three directories, and the archive path is exactly `flow/downloads/product_clips.zip`.

- [ ] **Step 2: Run path RED**

```bash
uv run pytest test/services/cloud_agent/test_storage.py -k "flow_archive or flow_staging or flow_quarantine" -v
```

Expected RED: `JobPaths` has no archive/staging/quarantine fields.

- [ ] **Step 3: GREEN — add deterministic local paths**

Extend `_paths()` and `prepare()` only. Keep current canonical paths unchanged so existing Canva/checkpoint code remains compatible.

- [ ] **Step 4: RED — safe canonical quarantine**

Add tests proving `quarantine_flow_canonical()`:

- returns `None` when no canonical files exist;
- moves only existing canonical `clip_01.mp4` … `clip_06.mp4` into one unique job-local quarantine directory;
- leaves `downloads/product_clips.zip` and `staging/` untouched for salvage;
- refuses a canonical symlink that resolves outside `flow/`;
- never removes unrelated Flow directory content.

- [ ] **Step 5: Run quarantine RED**

```bash
uv run pytest test/services/cloud_agent/test_storage.py -k "quarantine_flow_canonical" -v
```

Expected RED: the quarantine operation does not exist.

- [ ] **Step 6: GREEN — implement reversible quarantine**

Use a collision-resistant directory name such as `uuid4().hex`; validate every source and destination remains within the job Flow directory; move with `Path.replace()`. If safety validation fails, raise without moving any source.

- [ ] **Step 7: RED/GREEN — typed errors**

Add a focused assertion that both new errors are subclasses of `MediaValidationError`, then add the two empty typed classes and their docstrings.

- [ ] **Step 8: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_storage.py -v
uv run ruff check app/services/cloud_agent/storage.py app/services/cloud_agent/errors.py test/services/cloud_agent/test_storage.py
git diff --check
git add app/services/cloud_agent/storage.py app/services/cloud_agent/errors.py test/services/cloud_agent/test_storage.py
git commit -m "feat: add recoverable flow artifact storage"
```

---

## Task 3: Secure semantic ZIP extraction, validation, materialization, and salvage

**Files:**

- Create: `app/services/cloud_agent/flow_archive.py`
- Create: `test/services/cloud_agent/test_flow_archive.py`
- Modify: `app/services/cloud_agent/storage.py` only if a path helper proven necessary by RED

**Interfaces produced:**

```python
from typing import Literal, NamedTuple

class FlowArtifactRecovery(NamedTuple):
    paths: tuple[Path, ...]
    source: Literal["canonical", "archive", "staging"]

def materialize_flow_archive(
    archive_path: Path,
    paths: JobPaths,
    *,
    min_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> tuple[Path, ...]: ...

def recover_flow_artifacts(
    storage: CloudJobStorage,
    job_id: str,
    *,
    min_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> FlowArtifactRecovery | None: ...
```

`recover_flow_artifacts()` owns local recovery priority but no database or browser work.

- [ ] **Step 1: RED — semantic ordering is independent of ZIP member order**

Create an archive fixture in random member order (`clip 4.mp4`, `clip 1.mp4`, …), monkeypatch the module's `validate_video`, call `materialize_flow_archive()`, and assert canonical content/order is `clip_01.mp4` … `clip_06.mp4` by semantic number, not archive order.

- [ ] **Step 2: Run ordering RED**

```bash
uv run pytest test/services/cloud_agent/test_flow_archive.py -k "semantic_order" -v
```

Expected RED: `flow_archive` and the materializer do not exist.

- [ ] **Step 3: GREEN — minimal safe six-member materializer**

Normalize member basenames with Unicode NFKC and accept only an exact, case-insensitive `^clip\s+([1-6])\.mp4$` semantic basename. Create a fresh staging subdirectory for each attempt. Extract only selected video members, validate all six there, then materialize canonical files with same-filesystem `os.replace()`.

- [ ] **Step 4: RED — archive safety and set integrity**

Parameterize tests for:

- `/absolute/clip 1.mp4`;
- `../clip 1.mp4` and nested `a/../../clip 1.mp4`;
- Windows drive/UNC/backslash traversal forms;
- a ZIP symlink derived from `ZipInfo.external_attr`;
- encrypted entries (`flag_bits & 0x1`);
- duplicate semantic number with different paths/casing/Unicode;
- missing semantic number;
- `clip 0`, `clip 7`, and ambiguous names such as `clip 1 copy.mp4`;
- any extra `.mp4` beyond the exact six;
- media-validation failure for any staged file.

Assert `FlowArchiveValidationError`, no canonical set is exposed, and no member is written outside the job-local staging directory. Harmless non-video metadata may be ignored only after its path and entry type are proven safe; it is never extracted.

- [ ] **Step 5: Run safety RED**

```bash
uv run pytest test/services/cloud_agent/test_flow_archive.py -k "unsafe or duplicate or missing or ambiguous or validation" -v
```

Expected RED: one or more unsafe/incomplete archives are not rejected atomically.

- [ ] **Step 6: GREEN — complete ZIP validation**

Inspect every `ZipInfo` before extraction. Reject absolute, rooted, drive-qualified, parent-traversing, backslash-traversing, symlink, encrypted, duplicate, incomplete, ambiguous, and extra-video sets. Catch `BadZipFile`, `LargeZipFile`, extraction I/O, and media-validation failures and raise sanitized `FlowArchiveValidationError` without exposing remote URLs.

- [ ] **Step 7: RED — recovery priority and crash-window salvage**

Add tests for:

1. six valid canonical files return source `canonical` without reading the archive;
2. partial/invalid canonical files are quarantined before archive recovery;
3. a valid surviving ZIP reconstructs all canonical files after partial canonical materialization;
4. if the ZIP is invalid but a complete staged semantic `clip 1` … `clip 6` set validates, staging reconstructs canonical files;
5. partial/invalid staging returns `None` after quarantine and never masquerades as ready;
6. canonical validation uses the same size/dimension policy passed by workflow;
7. canonical, ZIP, and staging recovery never delete the only salvageable source before replacement succeeds.

- [ ] **Step 8: Run recovery RED**

```bash
uv run pytest test/services/cloud_agent/test_flow_archive.py -k "recover or salvage or canonical" -v
```

Expected RED: local recovery priority and salvage do not exist.

- [ ] **Step 9: GREEN — implement recovery without external side effects**

Recovery algorithm:

```text
validate complete canonical set
  -> return canonical
otherwise quarantine any canonical members
if archive exists
  -> safely rematerialize and return archive on success
  -> quarantine invalid archive without deleting staging
if one complete semantic staged set exists
  -> validate all, crash-recoverably materialize, return staging
otherwise
  -> quarantine invalid/partial staging and return None
```

Use unique staging generations so a failed attempt cannot be confused with a previous complete set. Quarantine is job-local and recoverable; do not use recursive deletion for uncertain artifacts.

- [ ] **Step 10: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_flow_archive.py test/services/cloud_agent/test_storage.py -v
uv run ruff check app/services/cloud_agent/flow_archive.py app/services/cloud_agent/storage.py test/services/cloud_agent/test_flow_archive.py test/services/cloud_agent/test_storage.py
git diff --check
git add app/services/cloud_agent/flow_archive.py app/services/cloud_agent/storage.py test/services/cloud_agent/test_flow_archive.py test/services/cloud_agent/test_storage.py
git commit -m "feat: recover semantic flow archives"
```

---

## Task 4: Long-wait production workspace lock without changing session endpoints

**Files:**

- Modify: `app/services/cloud_agent/browser.py`
- Modify: `test/services/cloud_agent/test_browser.py`

**Interface produced:**

```python
@contextmanager
def open(
    self,
    service: BrowserService,
    *,
    headed: bool | None = None,
    lock_timeout_seconds: float | None = None,
) -> Iterator[Any]: ...
```

- [ ] **Step 1: RED — per-open timeout override**

Extend the fake profile lock to record timeouts. Assert the default call still uses the manager's short timeout and a production call with `lock_timeout_seconds=1800.0` passes exactly that value to `ProfileLock.acquire()`.

- [ ] **Step 2: Run timeout RED**

```bash
uv run pytest test/services/cloud_agent/test_browser.py -k "lock_timeout" -v
```

Expected RED: `open()` rejects the new keyword or ignores it.

- [ ] **Step 3: GREEN — bounded override**

Resolve the effective timeout as the explicit override when not `None`, otherwise `self.lock_timeout_seconds`. Reject negative overrides. Do not change callers yet; current session providers therefore retain existing behavior.

- [ ] **Step 4: RED — second production owner waits for first**

Use two managers sharing one real temporary `ProfileLock`, a thread/event boundary, and short test timeouts. Hold the first context, start the second production open, assert the second has not entered, release the first, then assert the second enters. Avoid timing-only assertions except for a small bounded event timeout.

- [ ] **Step 5: GREEN — no extra code unless RED reveals a real lock defect**

The existing cross-process lock should satisfy this once the override is routed correctly. If not, use systematic debugging and correct only the proven lock defect with its own RED coverage.

- [ ] **Step 6: Verify session timeout compatibility and commit**

```bash
uv run pytest test/services/cloud_agent/test_browser.py test/services/cloud_agent/test_session.py -v
uv run ruff check app/services/cloud_agent/browser.py test/services/cloud_agent/test_browser.py
git diff --check
git add app/services/cloud_agent/browser.py test/services/cloud_agent/test_browser.py
git commit -m "feat: support production flow workspace waits"
```

---

## Task 5: Context-managed Flow workspace, semantic rename, and bulk ZIP download

**Files:**

- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`
- Reuse: `app/services/cloud_agent/flow_archive.py`

**Interfaces produced:**

```python
class FlowWorkspaceRun:
    def generate_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...]: ...

    def cleanup_and_verify_empty(self) -> None: ...

class GoogleFlowClient:
    @contextmanager
    def acquire_workspace(self, job: CloudJobRecord) -> Iterator[FlowWorkspaceRun]: ...
```

`FlowWorkspaceRun` owns the already-open page, service URL, timeouts, selectors, and archive materializer call. It does not own or import `CloudJobStore`.

- [ ] **Step 1: RED — workspace lock/context lifetime**

Update the fake browser manager to accept and record the per-open timeout. Add a test:

```python
with client.acquire_workspace(job) as workspace:
    assert browser.context_is_open
    assert browser.open_calls == [("google_flow", False, production_timeout)]
    assert workspace.page is page
assert browser.context_is_open is False
```

Also assert `sessions.ensure_service_ready("google_flow", job.id)` occurs before the long browser open and that no SQLite object is accepted by the provider constructor.

- [ ] **Step 2: Run context RED**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -k "workspace_context or lock_lifetime" -v
```

Expected RED: `acquire_workspace()` and `FlowWorkspaceRun` do not exist.

- [ ] **Step 3: GREEN — context boundary only**

Add the context manager, navigate once to the configured project, and yield a run object. Add `workspace_lock_timeout_seconds: float | None = None` to `GoogleFlowClient.__init__`; resolve `None` to the already-bounded `generation_timeout_seconds` value (currently 1800 seconds) and pass it only to the production browser open. Do not add a new config key. Keep session checks on their existing short lock path.

- [ ] **Step 4: RED — mandatory pre-clean and observable empty state**

Build a stateful fake page adapter with product clip cards and action log. Add tests proving:

- stale clips are all deleted before Generate;
- cleanup refreshes/reloads and waits for an observable zero-card state;
- Generate is never clicked when empty state cannot be verified;
- failure raises `FlowWorkspaceVerificationError`;
- a clean workspace still receives refresh/observable verification before Generate;
- the second workspace owner cannot inspect or clean while the first owns the browser/profile lock (integration with Task 4 fake/real lock).

Use role, accessible name, text, stable attributes, and observable card-count/state locators captured by Task 9/Task 14 discovery. Do not use coordinate-only actions.

- [ ] **Step 5: GREEN — pre-clean helper**

Implement one private page-object/helper boundary that:

1. enumerates observable product clip cards;
2. invokes each verified delete action;
3. confirms each removal or the resulting count;
4. reloads the project;
5. verifies zero product clips before returning.

Do not submit the prompt until this helper succeeds.

- [ ] **Step 6: RED — generation completion does not define order**

Retain existing tests for Agent mode, prompt fill, Generate click, progress polling, and bounded timeout. Change the result fixture so completion/card order is `4, 1, 6, 2, 5, 3`. Assert no canonical mapping or individual download occurs before semantic rename verification.

- [ ] **Step 7: GREEN — retain bounded generation only**

Reuse the proven Agent/Prompt/Generate selectors and observable `6 / 6` completion. Remove individual-download mapping and retry behavior only after their replacement ZIP tests are RED.

- [ ] **Step 8: RED — exact Agent rename instruction and semantic verification**

Add tests asserting:

- after generation, the Agent prompt receives exactly `เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ`;
- the code waits for observable Agent completion, then reloads;
- exactly one each `clip 1` … `clip 6` is required;
- missing, duplicate, and ambiguous semantic labels raise `FlowWorkspaceVerificationError`;
- semantic names, not card order, determine archive expectations.

- [ ] **Step 9: GREEN — rename and verify**

Use the same proven Agent prompt selector, exact instruction, and observable completion state. Reload and parse normalized visible/accessible product-clip names with an anchored semantic-number pattern. Reject anything other than one each 1–6.

- [ ] **Step 10: RED — one bulk Download Product Clips event**

Add tests proving:

- `page.expect_download()` is registered before clicking the exact verified bulk `Download Product Clips` control;
- only that bulk action is used; the old collection of generic Download buttons is not queried;
- the resulting download is saved to `paths.flow_archive_file`;
- `materialize_flow_archive()` is called with the saved archive and media policy;
- the returned tuple is canonical semantic order;
- no browser download event or an invalid archive becomes `FlowArchiveValidationError` and never reports success.

- [ ] **Step 11: Run bulk-download RED**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py -k "bulk or semantic or rename" -v
```

Expected RED: current provider queries generic individual Download buttons and maps locator order.

- [ ] **Step 12: GREEN — bulk ZIP boundary**

Register the Playwright event before the final bulk click, wait for completion, save only to the job-local archive path, and call Task 3's materializer. Do not log the source URL. Remove obsolete per-clip retry code and tests only in the same change that adds equivalent archive validation coverage.

- [ ] **Step 13: RED — post-job cleanup contract**

Add tests that `workspace.cleanup_and_verify_empty()` deletes all product clips, reloads, and returns only after observable zero. Any inability to prove empty raises `FlowWorkspaceVerificationError`.

- [ ] **Step 14: GREEN — cleanup method**

Reuse the same pre-clean helper so pre- and post-job empty verification cannot drift. The provider raises typed cleanup errors; workflow suppression belongs to Task 6.

- [ ] **Step 15: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_flow_archive.py -v
uv run ruff check app/services/cloud_agent/providers/google_flow.py app/services/cloud_agent/flow_archive.py test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_flow_archive.py
git diff --check
git add app/services/cloud_agent/providers/google_flow.py test/services/cloud_agent/test_google_flow.py
git commit -m "feat: automate shared flow workspace"
```

---

## Task 6: Workflow recovery, atomic FLOW_READY, cleanup suppression, and control ordering

**Files:**

- Modify: `app/services/cloud_agent/workflow.py`
- Modify: `app/services/cloud_agent/factory.py`
- Modify: `test/services/cloud_agent/test_workflow.py`
- Modify: `test/services/cloud_agent/test_workflow_preflight_context.py` only if its Flow fake uses the old protocol

**Protocol produced:**

```python
class FlowWorkspace(Protocol):
    def generate_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...]: ...

    def cleanup_and_verify_empty(self) -> None: ...

class FlowClient(Protocol):
    def acquire_workspace(
        self,
        job: CloudJobRecord,
    ) -> ContextManager[FlowWorkspace]: ...
```

- [ ] **Step 1: RED — normal run persists before cleanup while lock is held**

Replace `RecordingFlow` with a context-managed `RecordingFlowWorkspace` fake. Record an ordered event list from workspace enter, generation, store patch, cleanup, and exit. Assert:

```text
workspace_enter
generate
persist_FLOW_READY_unresolved_true
cleanup
persist_unresolved_false
workspace_exit
canva
```

Assert the first persistence is one `patch_job()` call containing status, checkpoint, step, progress, and `flow_cleanup_unresolved=True`.

- [ ] **Step 2: Run lifecycle RED**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -k "cleanup_unresolved or persists_before_cleanup" -v
```

Expected RED: workflow uses the old flat Flow call and cannot hold the workspace through persistence.

- [ ] **Step 3: GREEN — context-managed normal path**

At `TTS_READY`, perform local recovery first. If recovery is absent, set `FLOW_GENERATING`, acquire the workspace, generate, validate the canonical six, and patch `FLOW_READY + unresolved=true` once. Attempt cleanup in the same `with` block; on verified success patch `unresolved=false`. Exit the workspace before Canva.

- [ ] **Step 4: RED — valid canonical crash recovery performs zero generation**

Create a `TTS_READY` job with valid canonical clips. Assert:

- TTS is not called;
- workspace is acquired;
- `workspace.generate_and_download()` is not called;
- `FLOW_READY + unresolved=true` is persisted;
- cleanup is attempted;
- Canva continues.

- [ ] **Step 5: RED — ZIP/staging crash-window salvage performs zero generation**

Add the required regression:

```text
validated surviving ZIP or staged clip 1..6 set
+ partial canonical materialization
+ process restart at TTS_READY
-> partial canonical set quarantined
-> canonical six reconstructed and validated
-> workspace acquired
-> zero additional Flow generations
-> FLOW_READY/unresolved=true persisted
-> cleanup attempted
-> Canva continues
```

Cover ZIP and staged recovery in parameterized cases; assert the Flow fake's generation count remains zero.

- [ ] **Step 6: Run recovery RED**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -k "canonical_recovery or zip_recovery or staged_recovery or zero_additional_flow" -v
```

Expected RED: current workflow always calls paid Flow generation from `TTS_READY`.

- [ ] **Step 7: GREEN — route Task 3 recovery before generation**

Call `recover_flow_artifacts()` before acquiring the workspace. A recovered complete set skips generation but still acquires the workspace for checkpoint-plus-cleanup. A missing/partial/invalid unsalvageable set falls through to workspace pre-clean and generation; provider pre-clean is mandatory and not controlled by job flags.

- [ ] **Step 8: RED — cleanup failure is historical state, not job failure**

Make `cleanup_and_verify_empty()` raise `FlowWorkspaceVerificationError`. Assert:

- durable checkpoint remains `FLOW_READY`;
- `flow_cleanup_unresolved` remains `True`;
- canonical clips remain;
- Canva is called and the job may reach `COMPLETED`;
- no second Flow generation occurs;
- error text is sanitized in logs and is not copied into a terminal job failure.

Also simulate an interruption after the first patch but before cleanup. Reload at `FLOW_READY` and assert Flow is never reopened or regenerated; Canva resumes from the canonical artifacts.

- [ ] **Step 9: GREEN — suppress only post-checkpoint cleanup failure**

Wrap only the cleanup/empty-verification operation after the durable patch. Catch the provider's typed cleanup failure and ordinary operational exceptions at that boundary, log a sanitized warning, leave `flow_cleanup_unresolved=True`, and continue. Do not suppress pre-clean, generation, archive validation, or pre-checkpoint failures.

- [ ] **Step 10: RED — pause/cancel occurs after cleanup attempt**

Have the fake set `PAUSE` or `CANCEL` immediately after the durable FLOW_READY patch. Assert cleanup is attempted while the workspace remains held before `_control_boundary()` changes status. Assert neither Canva nor a new generation runs after the control request is honored.

- [ ] **Step 11: GREEN — move the control boundary**

Place the control check after cleanup attempt and workspace release. Preserve all earlier preflight/TTS control boundaries.

- [ ] **Step 12: RED — second job waits and begins only after observable empty release**

Add integration-style concurrency cases using two clients/workflows with one `google_flow` `ProfileLock` and event-controlled fake pages. Assert job B cannot inspect, clean, generate, rename, or download while job A owns the lock. Cover both outcomes for A: successful post-job cleanup and failed post-job cleanup. In either case, after A releases, B enters, performs its own pre-clean and observable zero verification, and only then submits generation.

This test must use bounded events/joins and no paid service.

- [ ] **Step 13: GREEN — route factory to new protocol**

Update only the existing `config.app` composition in `factory.py`. Do not create another config loader. Reuse existing timeout/dimension settings and `CloudJobStorage` instance.

- [ ] **Step 14: Preserve existing checkpoint behavior**

Run and, only where protocol mechanics require it, adapt existing tests for:

- preflight before paid work;
- `TTS_READY` resume without duplicate TTS;
- `FLOW_READY` resume without duplicate TTS or Flow;
- HUMAN_REQUIRED checkpoint preservation;
- pause/resume/cancel;
- narration over-policy stopping before Flow;
- final-validation failure retaining source clips;
- cleanup only after `FINAL_VALIDATED` for local sources;
- final Canva assembly unchanged.

Do not weaken their assertions.

- [ ] **Step 15: Verify and commit**

```bash
uv run pytest test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_workflow_preflight_context.py -v
uv run ruff check app/services/cloud_agent/workflow.py app/services/cloud_agent/factory.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_workflow_preflight_context.py
git diff --check
git add app/services/cloud_agent/workflow.py app/services/cloud_agent/factory.py test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_workflow_preflight_context.py
git commit -m "feat: recover and checkpoint flow workspace runs"
```

---

## Task 7: No-paid integration regression and compatibility gate

**Files:** No planned production changes. Any failure must be diagnosed before a narrowly covered correction.

- [ ] **Step 1: Focused lifecycle suite**

```bash
uv run pytest \
  test/services/cloud_agent/test_models.py \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_storage.py \
  test/services/cloud_agent/test_flow_archive.py \
  test/services/cloud_agent/test_browser.py \
  test/services/cloud_agent/test_session.py \
  test/services/cloud_agent/test_google_flow.py \
  test/services/cloud_agent/test_workflow.py \
  test/services/cloud_agent/test_workflow_preflight_context.py \
  test/services/cloud_agent/test_worker.py \
  -v
```

Expected: PASS with fake providers/pages only and zero paid calls. The existing worker lease-renewal test must still prove the lease stays active while a workflow is blocked in a long external/workspace step.

- [ ] **Step 2: Cloud Agent regression**

```bash
uv run pytest test/services/cloud_agent test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py -v
```

Expected: PASS. Confirm session-control tests still report busy on a short bounded lock timeout and Canva tests are unchanged.

- [ ] **Step 3: Full repository regression**

```bash
uv run pytest -v
```

Expected: PASS at the repository's existing coverage gate. If a failure is unrelated, preserve the evidence and do not change unrelated production behavior.

- [ ] **Step 4: Ruff and diff safety**

```bash
uv run ruff check \
  app/models/cloud_agent.py \
  app/services/cloud_agent/browser.py \
  app/services/cloud_agent/errors.py \
  app/services/cloud_agent/factory.py \
  app/services/cloud_agent/flow_archive.py \
  app/services/cloud_agent/job_store.py \
  app/services/cloud_agent/providers/google_flow.py \
  app/services/cloud_agent/storage.py \
  app/services/cloud_agent/workflow.py \
  test/services/cloud_agent/test_browser.py \
  test/services/cloud_agent/test_flow_archive.py \
  test/services/cloud_agent/test_google_flow.py \
  test/services/cloud_agent/test_job_store.py \
  test/services/cloud_agent/test_models.py \
  test/services/cloud_agent/test_storage.py \
  test/services/cloud_agent/test_workflow.py \
  test/services/cloud_agent/test_workflow_preflight_context.py
git diff --check
git status --short
```

Expected: PASS; only intended lifecycle files are changed.

- [ ] **Step 5: Non-paid live selector/readiness smoke**

On the Ubuntu VPS, use the existing authenticated Google Flow profile and configured dedicated project under the production workspace lock. Perform only:

- session classification;
- project open;
- product-clip inventory;
- stale-product cleanup if present;
- refresh and observable zero-product verification;
- lock release;
- second acquisition proving the project is still observably empty.

Do not click Generate, do not call TTS, do not submit the Agent rename prompt, and do not touch Canva. If login/CAPTCHA/2FA/device/OAuth interaction appears, stop with `HUMAN_REQUIRED_AUTH`.

- [ ] **Step 6: Commit any test-only gate evidence**

If Steps 1–5 required no code changes, no extra commit is needed. If a real compatibility defect was found, first add a focused RED regression, apply one minimal fix, rerun all gates, and commit with a message describing that defect.

---

## Task 8: Push and CI checkpoint before paid Task 14 continuation

- [ ] **Step 1: Verify commit history and clean tree**

```bash
git log --oneline --decorate -8
git status --short --branch
```

Expected: Design Spec commit plus the implementation commits above; clean working tree.

- [ ] **Step 2: Push the approved feature branch**

```bash
git push origin feature/cloud-video-agent
git merge-base --is-ancestor HEAD origin/feature/cloud-video-agent
```

Expected: remote contains the final implementation commit.

- [ ] **Step 3: Wait for GitHub Actions**

Wait for Windows smoke, Python 3.11, and Python 3.13 checks associated with the final SHA. If a check fails, inspect the exact failing job/log, establish root cause, add RED coverage for production behavior changes, make one minimal correction, rerun local gates, push, and wait again.

- [ ] **Step 4: Stop at the paid gate**

Do not run real TTS or Flow generation without a fresh explicit paid authorization. Report sanitized evidence for:

- local and CI test counts;
- Ruff result;
- session/project empty-state readiness;
- workspace-lock exclusivity;
- recovery scenarios proving zero duplicate generations;
- current branch/SHA and clean tree;
- paid gate status.

Do not start Task 15 legacy cleanup.

---

## Required RED Evidence Ledger

Before each GREEN change, preserve the focused pytest command and the intended failure summary. At minimum the implementation handoff must contain RED evidence for:

1. missing `flow_cleanup_unresolved` model/store behavior;
2. missing archive/staging/quarantine paths;
3. shuffled semantic ZIP ordering;
4. unsafe/incomplete/duplicate ZIP rejection;
5. canonical/ZIP/staging crash recovery;
6. per-open production lock timeout;
7. context-managed Flow workspace lifetime;
8. mandatory stale-clip pre-clean and observable zero state;
9. out-of-order completion followed by exact Agent semantic rename;
10. bulk Download Product Clips event registered before click;
11. atomic FLOW_READY/unresolved persistence before cleanup;
12. post-checkpoint cleanup failure continuing to Canva;
13. pause/cancel honored only after cleanup attempt;
14. `FLOW_READY` restart with no regeneration;
15. partial canonical plus validated ZIP/staging restart with zero additional generation;
16. second job blocked until the first releases an observably empty workspace.

No test may be rewritten merely to accommodate implementation output. Unexpected failures follow root cause → smallest safe fix → focused verification → full regression.
