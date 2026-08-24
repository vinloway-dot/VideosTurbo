# Safe Pre-Flow Retry from Canonical Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Before every production-code behavior change, use `superpowers:test-driven-development`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow an operator to requeue the same `FAILED / TTS_READY` job after a provably pre-paid transient Google Flow editor failure, reusing one valid canonical narration without issuing another TTS or Flow Generate request.

**Architecture:** A small `PreFlowRetryService` performs only durable-record and job-local-artifact eligibility checks, revalidates canonical audio with the existing probe/timing policy, and atomically requeues an eligible job. FastAPI exposes that service as an explicit Retry action; the existing workflow remains the only owner of Flow preparation/fence/Generate and the existing recovery path remains authoritative for Flow artifacts. `GoogleFlowClient` strengthens its already-observable editor-ready boundary to require consecutive stable actionable observations.

**Tech Stack:** Python 3.11+, FastAPI, SQLite `CloudJobStore`, ffprobe media validation, Playwright sync API, Streamlit, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md` §3.12.

## Global Constraints

- Keep `videosturbo-worker.service` inactive for all implementation and non-paid verification.
- Never call TTS, Google Flow Generate, Flow rename/download/delete/pre-clean, or Canva during this task.
- Do not manually edit SQLite or create an operational retry script; use only the application controller/service path.
- Preserve the paid-generation fence: `flow_generation_unresolved=true` is reconciliation-only and cannot enter the fresh retry path.
- Preserve `c604f5d5-c206-4d49-bad2-cac59e2815a2` and all remote Flow assets unchanged.
- The target live verification job is `7c76329b-c533-453d-8b2e-9533c2642153`; it must receive no new TTS or Flow Generate request.
- No new status, database column, config loader, browser-profile access, or automatic retry loop.
- Do not start Task 15; Task 14 Gate F remains incomplete.

---

### Task 1: RED — define pure pre-fence retry eligibility and durable transition

**Files:**
- Create: `app/services/cloud_agent/retry.py`
- Modify: `app/services/cloud_agent/errors.py`
- Create: `test/services/cloud_agent/test_retry.py`
- Modify: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**

```python
class PreFlowRetryEligibilityError(Exception): ...

class PreFlowRetryService:
    def __init__(
        self,
        store: CloudJobStore,
        storage: CloudJobStorage,
        *,
        tts_min_duration: float,
        canva_min_playback_speed: float,
    ) -> None: ...

    def retry(self, job_id: str) -> CloudJobRecord: ...
```

- [ ] **Step 1: Write failing pure-retry tests**

  Create a `FAILED / TTS_READY` record with canonical `audio/voice.mp3`, error code `FLOW_WORKSPACE_VERIFICATION_FAILED`, false Flow flags, no Flow artifact evidence, and an intentionally stale but model-valid timing triplet. Mock only ffprobe validation to return a decimal `MediaProbe(63.936, ...)`. Assert `retry(job.id)` returns the same id with `status=QUEUED`, `checkpoint=TTS_READY`, false flags, cleared errors/control request, canonical `voice_file`, and recomputed `63.936`, `0.9384384384384384`, `63.936` timing. Assert the TTS and Flow fakes have zero calls because the retry service has neither dependency.

  Add a second test that performs this requeue, lets the existing workflow encounter a pre-fence `FlowWorkspaceVerificationError`, requeues it again, and asserts every run preserved the same voice file, TTS calls stayed zero, and Flow Generate calls stayed zero.

- [ ] **Step 2: Write failing refusal/recovery-safety tests**

  Add parametrized RED cases for each individually observable disqualifier:

  ```python
  # unresolved generation is never converted to fresh retry
  job = failed_tts_ready_job(flow_generation_unresolved=True)
  with pytest.raises(PreFlowRetryEligibilityError, match="reconciliation"):
      service.retry(job.id)

  # missing or invalid canonical audio fails before any state transition
  paths.voice_file.unlink()
  with pytest.raises(PreFlowRetryEligibilityError, match="canonical narration"):
      service.retry(job.id)

  # a canonical clip, ZIP, staged file, or quarantined Flow artifact blocks fresh retry
  paths.flow_files[0].write_bytes(b"possible-flow-output")
  with pytest.raises(PreFlowRetryEligibilityError, match="Flow artifact"):
      service.retry(job.id)
  ```

  Assert all refusals leave `FAILED / TTS_READY`, the original error, flags, and audio untouched. Add an explicit regression that a valid ZIP/staging set is not deleted or reclassified by the retry service; existing `recover_flow_artifacts()` workflow tests remain the authoritative recovery behavior.

- [ ] **Step 3: Run RED evidence**

  Run:

  ```bash
  uv run pytest test/services/cloud_agent/test_retry.py test/services/cloud_agent/test_workflow.py -k 'retry or flow_workspace_error_before_paid_fence or tts_ready_partial_canonical_salvage' -v
  ```

  Expected: failures are missing `PreFlowRetryService` / retry endpoint behavior, not import, fixture, or media-test setup errors. Preserve the command output in the work log before writing production code.

### Task 2: GREEN — implement the fail-closed service without a migration

**Files:**
- Create: `app/services/cloud_agent/retry.py`
- Modify: `app/services/cloud_agent/errors.py`
- Test: `test/services/cloud_agent/test_retry.py`

**Interfaces:**

```python
def _has_flow_artifact_evidence(paths: JobPaths) -> bool: ...

def retry(self, job_id: str) -> CloudJobRecord:
    """Validate a pure pre-fence failure and patch only the same job to QUEUED."""
```

- [ ] **Step 1: Implement typed refusal and read-only evidence checks**

  Add `PreFlowRetryEligibilityError` as a Cloud Agent typed error. Implement the service with the existing `CloudJobStore`, `CloudJobStorage`, `validate_audio`, and `calculate_adaptive_timing`. Require exactly `FAILED`, `TTS_READY`, no active lease, `FLOW_WORKSPACE_VERIFICATION_FAILED`, both false flags, and a canonical job-owned normal `voice.mp3`. Treat any named canonical clip, archive/download content, staging entry, or Flow quarantine entry as side-effect evidence and raise the typed refusal without moving/quarantining/deleting anything.

- [ ] **Step 2: Implement timing revalidation and one normal state update**

  Probe the canonical voice with the configured minimum duration. Recompute timing with `calculate_adaptive_timing(..., min_playback_speed=...)`; if the policy rejects the real audio, refuse retry. Otherwise call exactly one existing `store.patch_job()` to set `QUEUED`, `current_step="queued"`, `control_request=NONE`, clear the prior error, and persist the recomputed timing. Preserve the original checkpoint, canonical voice path, Flow flags, script, prompt, and all source artifacts. Do not add a SQLite migration.

- [ ] **Step 3: Run focused GREEN tests**

  Run the Task 1 command again and then:

  ```bash
  uv run pytest test/services/cloud_agent/test_retry.py test/services/cloud_agent/test_workflow.py -v
  ```

  Expected: all retry, no-duplicate-TTS, fence, salvage, and `FLOW_READY` restart tests pass.

### Task 3: RED/GREEN — expose one explicit FastAPI/Streamlit retry action

**Files:**
- Modify: `app/services/cloud_agent/factory.py`
- Modify: `app/controllers/v1/cloud_agent.py`
- Modify: `webui/cloud_agent.py`
- Modify: `test/services/test_cloud_agent_controller.py`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**

```python
def build_pre_flow_retry_service() -> PreFlowRetryService: ...

@router.post("/cloud-agent/jobs/{job_id}/retry")
def retry_cloud_agent_job(...) -> Response: ...
```

- [ ] **Step 1: Write controller/UI RED tests**

  Extend the required controller route contract with `POST /api/v1/cloud-agent/jobs/{job_id}/retry`. Add an integration test using a temporary store/storage and deterministic audio probe that confirms an eligible failed job becomes queued without invoking browser, TTS, or Flow. Add tests that unresolved generation, missing audio, artifact evidence, non-`FAILED` status, and wrong pre-fence error code return HTTP 409 with a sanitized message and do not modify the record.

  Add a Streamlit unit test that verifies the Cloud Agent panel offers a `Retry` control and routes it only to `POST jobs/{job_id}/retry`. Add failure-display coverage for a 409 body proving UI shows a short retry/reconciliation reason rather than a raw stack trace.

- [ ] **Step 2: Run controller/UI RED evidence**

  Run:

  ```bash
  uv run pytest test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py -k 'retry' -v
  ```

  Expected: missing retry dependency/route/control failures, not an unrelated FastAPI or Streamlit fixture error.

- [ ] **Step 3: Implement minimal composition and handlers**

  Add `build_pre_flow_retry_service()` to `factory.py`, sourcing only the existing `config.app` values already used by `build_workflow()`. Add the controller dependency and endpoint; map only `PreFlowRetryEligibilityError` to `HttpException(status_code=409)` with its sanitized message. Do not open a browser or create a workflow/worker in the request.

  Add a `Retry` button to the existing Cloud Agent controls. Reuse `_api`; catch its request failure and render its sanitized API message with `st.error`. On success, render the returned job and a caption that the canonical narration will be reused. Do not read SQLite or browser profiles from Streamlit.

- [ ] **Step 4: Run controller/UI GREEN tests**

  Re-run the Task 3 RED command and then:

  ```bash
  uv run pytest test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py -v
  ```

### Task 4: RED/GREEN — require a stable non-paid Flow editor boundary

**Files:**
- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`
- Modify: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**

```python
def _wait_for_settled_editor(self, page: Any) -> None:
    """Require settled_poll_count consecutive actionable observations."""
```

- [ ] **Step 1: Write Flow readiness RED tests**

  Use the existing fake Flow page to make `_is_editor_actionable()` true once then false, and assert acquisition does not return on the transient observation. Add a test requiring exactly `settled_poll_count` consecutive observations before it returns. Add a page that visibly contains the observed `Application error: a client-side exception has occurred` / undefined-service error while stale controls remain; assert it is non-actionable and times out with `FlowWorkspaceVerificationError` before `prepare_for_generation`, Agent prompt fill, fence, or Generate.

  Add a workflow regression starting from a retry-eligible queued/TTS-ready job whose workspace acquisition raises this error. Assert it returns to `FAILED / TTS_READY` with `FLOW_WORKSPACE_VERIFICATION_FAILED`, preserves canonical voice, keeps both Flow flags false, and has zero TTS/Generate calls. A subsequent retry after a healthy fake editor must reuse the same voice and enter only normal Flow preparation; intercept before the paid fence/Generate and assert zero paid calls.

- [ ] **Step 2: Run RED evidence**

  Run:

  ```bash
  uv run pytest test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_workflow.py -k 'settled_editor or pre_paid_retry or agent_activation or paid_fence' -v
  ```

  Expected: the transient one-poll editor currently returns too early; workflow assertions fail only for the intended retryable-state behavior.

- [ ] **Step 3: Implement one bounded stability loop**

  Add one small observable fatal-application-error predicate for the proven visible Flow error text and consult it from `_is_editor_actionable()`. Change only `_wait_for_settled_editor()` so `settled_poll_count` consecutive calls to that existing actionable contract are required. Reset the count whenever any required observable state disappears. Retain the existing bounded editor timeout and landing-page launch action. Do not add browser lifecycle changes, retries, or paid actions.

- [ ] **Step 4: Run focused Flow/workflow GREEN tests**

  Re-run the Task 4 RED command, then:

  ```bash
  uv run pytest test/services/cloud_agent/test_google_flow.py test/services/cloud_agent/test_workflow.py -v
  ```

  Verify existing Agent activation, card pre-clean, generation fence, reconciliation, archive recovery, and `FLOW_READY` restart tests stay green.

### Task 5: Documentation, safe live non-paid check, and full verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md`
- Create: `docs/superpowers/plans/2026-08-24-retry-pre-flow-failures.md`
- Modify: only production/test files proven necessary by Tasks 1–4

- [ ] **Step 1: Review documentation and diff before live check**

  Confirm the spec records the exact endpoint, the fail-closed eligibility predicates, re-probe/recompute timing behavior, artifact/reconciliation block, no new schema/status/automatic retries, WebUI behavior, and consecutive editor-ready observations. Search both documents for `TODO`, `TBD`, and contradictory references to v2.5/v2.6; resolve each before proceeding.

- [ ] **Step 2: Verify the target job through the supported path**

  With `videosturbo-worker.service` confirmed inactive, read job `7c76329b-c533-453d-8b2e-9533c2642153` through the store/API only to prove it still meets the pure pre-fence predicates. Invoke `POST /api/v1/cloud-agent/jobs/{id}/retry`; do not patch SQLite. Confirm it returns `QUEUED / TTS_READY`, retains the exact canonical voice/timing (or the same policy-recomputed values), and emits zero TTS/Flow Generate calls. Do not start the worker or run the workflow past the pre-paid boundary. If the vendor is checked, use only its existing non-paid session/readiness path; never pre-clean, prepare Agent prompt, or click Generate.

- [ ] **Step 3: Run required repository verification**

  Run in this order:

  ```bash
  uv lock --check
  uv sync --frozen
  uv run python -m compileall app webui
  uv run ruff check app webui test
  uv run pytest
  git diff --check
  ```

  Record the full-suite count and coverage. Coverage must be at least 70%. Investigate any unexpected failure before changing code; use a new failing regression for each production correction.

- [ ] **Step 4: Commit, push, and wait for CI**

  Inspect `git diff --check` and `git status --short`; commit only the spec, plan, retry service/error/factory/controller/UI, and their tests as:

  ```bash
  git add docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md \
      docs/superpowers/plans/2026-08-24-retry-pre-flow-failures.md \
      app/services/cloud_agent/retry.py app/services/cloud_agent/errors.py \
      app/services/cloud_agent/factory.py app/controllers/v1/cloud_agent.py \
      app/services/cloud_agent/providers/google_flow.py webui/cloud_agent.py \
      test/services/cloud_agent/test_retry.py \
      test/services/cloud_agent/test_workflow.py \
      test/services/cloud_agent/test_google_flow.py \
      test/services/test_cloud_agent_controller.py \
      test/services/test_cloud_agent_webui.py
  git commit -m "fix: retry pre-flow failures from canonical audio"
  git push origin feature/cloud-video-agent
  ```

  Verify `origin/feature/cloud-video-agent` contains the commit SHA and wait for Windows smoke, Python 3.11, and Python 3.13. Keep the PR Draft and do not start Task 15 or consume a paid Flow attempt.
