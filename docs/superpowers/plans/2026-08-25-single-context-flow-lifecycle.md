# Single-Context Flow Lifecycle v3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Before every production-code behavior change, use `superpowers:test-driven-development`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Google Flow session verification, project hydration recovery, and all workspace work in one locked persistent browser context so a successful throwaway session check cannot race a later project boot.

**Architecture:** `GoogleFlowClient.acquire_workspace()` becomes the sole owner of its Flow browser context. It warms the Flow landing route, validates the session on the actual project page, and uses at most two same-page reloads to recover a non-hydrated or fatal project before yielding `FlowWorkspaceRun`. Generic `BrowserSessionProvider.check_session()` remains a separate coarse preflight diagnostic; `CloudAgentWorkflow` retains fence persistence and no provider writes SQLite.

**Tech Stack:** Python 3.11+, Playwright sync API, pytest deterministic page doubles, Ruff, existing CloudJob workflow/fence contracts.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md` §3.13.

## Global Constraints

- Keep `videosturbo-worker.service` inactive until CI and five non-paid live lifecycle runs pass.
- Preserve job `7c76329b-c533-453d-8b2e-9533c2642153` at `TTS_READY` with `flow_generation_unresolved=true`; never mutate SQLite during implementation or live reliability proof.
- Do not call TTS, Retry, Generate, Agent rename, ZIP download, remote deletion, or pre-clean until the separately authorized reconciliation stage.
- Use the existing `google_flow` persistent profile lock; never open concurrent Chrome contexts for that profile.
- Keep the v2.9 hidden-video completion contract unchanged: no `video:visible` requirement and no media `readyState` requirement.
- Keep generic preflight/session APIs, Canva, TTS, job schema, worker queue, and Task 15 out of scope.
- Do not log credentials, cookies, tokens, signed URLs, or private browser data.

---

### Task 1: RED-test one owned Flow lifecycle and hydration recovery

**Files:**
- Modify: `test/services/cloud_agent/test_google_flow.py`

**Interfaces:**

```python
class GoogleFlowClient:
    def acquire_workspace(self, job: CloudJobRecord) -> Iterator[FlowWorkspaceRun]: ...
    def _flow_home_url(self) -> str: ...
    def _verify_workspace_session(self, page: Any, job_id: str) -> None: ...
    def _hydrate_project_workspace(
        self, page: Any, *, flow_generation_unresolved: bool
    ) -> None: ...
```

`_verify_workspace_session()` consumes only the passed project page and existing
`classify_google_flow_session()` / security classification. It must not call
`SessionManager.ensure_service_ready()` or open a browser. `_hydrate_project_workspace()` consumes that same page and may call `page.reload()` at most twice.

- [ ] **Step 1: Extend only the deterministic Flow page/browser doubles needed to model home navigation and per-reload hydration.**

  Add a `home_progress_html` argument to `FakePage`; make `goto()` switch the
  active HTML sequence when the URL ends in `/tools/flow`, and retain the
  existing `reload_progress_html` behavior for the project page. Keep
  `FakeBrowserManager.open_calls` as the observable ownership record.

- [ ] **Step 2: Add RED tests for a single owned warm-home lifecycle.**

  ```python
  def test_google_flow_workspace_warms_home_and_hydrates_project_in_one_context():
      page = FakePage(
          progress_html=["<div>Ready</div>"],
          home_progress_html=["<main>Flow home</main>"],
      )
      client, sessions = _client(page)

      with client.acquire_workspace(_job()) as workspace:
          assert workspace.page is page
          assert client.browser.context_is_open is True

      assert sessions.calls == []
      assert client.browser.open_calls == [("google_flow", None, 30.0)]
      assert [url for url, _ in page.goto_calls] == [
          "https://labs.google/fx/tools/flow",
          "https://labs.google/fx/tools/flow/project/demo",
      ]
  ```

  This fails against the current code because it invokes the external session
  manager and navigates directly to the project without warmup.

- [ ] **Step 3: Add RED tests for all reconciliation hydration outcomes.**

  ```python
  def test_fenced_workspace_recovers_initial_empty_editor_with_same_page_reload():
      page = FakePage(
          progress_html=["<div>Loading</div>"],
          reload_progress_html=[["<div>Ready</div>"]],
      )
      client, _ = _client(page, editor_ready_timeout_seconds=0.02)

      with client.acquire_workspace(_job(flow_generation_unresolved=True)):
          pass

      assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
      assert ("click", "generate") not in page.actions
      assert not any(action[0] == "fill" for action in page.actions)
      assert not any(action[0] in {"click", "check"} and "delete" in action for action in page.actions)
  ```

  Add companion tests for fatal → one reload → hydrated; empty → empty →
  hydrated after exactly two reloads; and persistent empty/fatal after the
  second reload raising `FlowWorkspaceVerificationError` with zero fill,
  Generate, media-card, or delete actions.

- [ ] **Step 4: Add RED regressions for fresh-path emptiness and same-page session failures.**

  Add a fresh (`flow_generation_unresolved=False`) test with an observable
  empty-state shell proving no hydration reload occurs solely because card count
  is zero. Add project-page session-expired and active-security-challenge tests
  asserting a typed human/session failure, one context only, and no reload or
  paid action. Keep the existing v2.9 hidden-video completion and reconciliation
  tests unchanged as regressions.

- [ ] **Step 5: Run the RED subset and preserve the intended failures.**

  Run:

  ```bash
  uv run pytest test/services/cloud_agent/test_google_flow.py \
    -k 'warms_home or fenced_workspace_recovers or two_reload or persistent_empty or workspace_session' -v
  ```

  Expected: failures identify the current separate `ensure_service_ready()` /
  direct-project lifecycle and lack of fenced same-context recovery, not fixture
  or import errors.

### Task 2: GREEN one-context warmup, page session verification, and unified hydration

**Files:**
- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`

**Interfaces:**

```python
def classify_google_flow_session(*, url: str, html: str) -> ServiceSessionStatus: ...

class GoogleFlowClient:
    def _flow_home_url(self) -> str: ...
    def _verify_workspace_session(self, page: Any, job_id: str) -> None: ...
    def _hydrate_project_workspace(
        self, page: Any, *, flow_generation_unresolved: bool
    ) -> None: ...
```

- [ ] **Step 1: Implement the safe derived Flow home URL.**

  Parse the already configured project URL with `urllib.parse.urlsplit`; retain
  scheme, host, and the path prefix before `/project/`; remove query/fragment.
  Raise `FlowWorkspaceVerificationError` when the configured URL is not a
  usable Flow project route. Do not hard-code a locale or design/project id.

- [ ] **Step 2: Implement page-local workspace session verification.**

  On the supplied project page, call the existing
  `classify_google_flow_session(url=page.url, html=page.content())`. Return only
  `READY`; map `SESSION_EXPIRED`, login, CAPTCHA, two-factor, and verification
  statuses to the existing `HumanRequiredError` boundary with the supplied job
  id; map `ERROR` to `FlowWorkspaceVerificationError`. This helper performs no
  browser open, session-manager call, repair, click, or credential access.

- [ ] **Step 3: Implement one unified same-context hydration loop.**

  In `_hydrate_project_workspace()`, call `_wait_for_settled_editor(page)` and
  return on success. On `_DirectLinkFatalPageError`, or on an ordinary
  `FlowWorkspaceVerificationError` after its bounded observation, call
  `page.reload(wait_until="domcontentloaded")` while fewer than two reloads
  have occurred, then retry on that exact page. At the limit, raise
  `FlowWorkspaceVerificationError("Google Flow project editor could not be verified")`.
  The algorithm is the same for fenced and fresh jobs; the boolean is retained
  only as an explicit safety/documentation parameter so future callers cannot
  mistake reload recovery for permission to generate.

- [ ] **Step 4: Rewire `acquire_workspace()` without changing Flow work APIs.**

  Remove the authoritative `self.sessions.ensure_service_ready()` call from
  `acquire_workspace()`. Inside its single existing `browser.open()` block:
  select its existing page; `goto(self._flow_home_url(), wait_until="domcontentloaded")`;
  `goto(self.service_url, wait_until="domcontentloaded")`;
  verify the session on that project page; hydrate using the unified loop; then
  yield the unchanged `FlowWorkspaceRun`. Keep `SessionManager` constructor
  injection and generic `GoogleFlowSessionProvider` available for independent
  preflight checks.

- [ ] **Step 5: Run the RED subset to GREEN, then the provider module.**

  ```bash
  uv run pytest test/services/cloud_agent/test_google_flow.py \
    -k 'warms_home or fenced_workspace_recovers or two_reload or persistent_empty or workspace_session or hidden_video' -v
  uv run pytest test/services/cloud_agent/test_google_flow.py -v
  ```

  Confirm the existing direct-fatal tests now describe the unified two-reload
  contract and remove only assertions that contradict the v3.0 approved fenced
  reload behavior.

### Task 3: Preserve workflow, session, fence, and retry boundaries

**Files:**
- Modify: `test/services/cloud_agent/test_google_flow.py`
- Test: `test/services/cloud_agent/test_session.py`
- Test: `test/services/cloud_agent/test_workflow.py`
- Test: `test/services/cloud_agent/test_retry.py`

**Interfaces:**

```python
class FlowWorkspaceRun:
    def reconcile_and_download(
        self, job: CloudJobRecord, paths: JobPaths, expected_count: int = 6
    ) -> tuple[Path, ...]: ...

class CloudAgentWorkflow:
    def run(self, job_id: str, worker_id: str) -> CloudJobRecord: ...
```

- [ ] **Step 1: Add/adjust only behavior regressions exposed by the new lifecycle.**

  Assert fenced workspace acquisition may reload but `reconcile_and_download()`
  still invokes no fresh prompt/Generate/pre-clean. Assert the paid fence remains
  durable before fresh submission, existing retry keeps its false-fence
  eligibility restriction, and session-manager generic preflight tests still
  exercise their independent provider contract.

- [ ] **Step 2: Run focused cross-layer regressions.**

  ```bash
  uv run pytest \
    test/services/cloud_agent/test_google_flow.py \
    test/services/cloud_agent/test_session.py \
    test/services/cloud_agent/test_workflow.py \
    test/services/cloud_agent/test_retry.py -v
  ```

  Expected: no workflow/schema/retry production modification is needed; any
  failure must be diagnosed before changing files outside `google_flow.py`.

### Task 4: Verify, commit, push, CI, and non-paid live lifecycle proof

**Files:** all files changed by Tasks 1–3 only.

- [ ] **Step 1: Run fresh repository verification.**

  ```bash
  uv lock --check
  uv sync --frozen
  uv run python -m compileall app webui
  uv run ruff check app webui test
  uv run pytest
  uv run coverage run -m pytest
  uv run coverage report
  git diff --check
  ```

  Require total coverage at least 70%, a clean Ruff result, and zero test
  failures before committing production behavior.

- [ ] **Step 2: Inspect the diff and commit implementation only.**

  ```bash
  git diff -- app/services/cloud_agent/providers/google_flow.py \
    test/services/cloud_agent/test_google_flow.py
  git add app/services/cloud_agent/providers/google_flow.py \
    test/services/cloud_agent/test_google_flow.py
  git commit -m "fix: keep flow lifecycle in one browser context"
  ```

- [ ] **Step 3: Push and verify CI.**

  Push `feature/cloud-video-agent`, verify local `HEAD` equals
  `origin/feature/cloud-video-agent`, and wait for the Windows smoke, Python
  3.11, and Python 3.13 checks to pass.

- [ ] **Step 4: Run the non-paid live proof only after CI passes.**

  Keep Worker inactive. For five sequential runs, acquire the Flow profile lock
  once per run and use one persistent context for home warmup, project page
  session verification, hydration recovery, six-card inventory, three v2.9
  fingerprint polls, and `_wait_for_generation(page, 6)`. Record only
  sanitized counts/statuses and require 5/5 healthy runs with exactly one
  workspace context each. Do not rename, download, delete, pre-clean, Generate,
  call TTS, or mutate SQLite.

- [ ] **Step 5: Continue only on the approved gate.**

  If the live proof is 5/5, resume only job
  `7c76329b-c533-453d-8b2e-9533c2642153` through its reconciliation-only path,
  preserving `flow_generation_unresolved=true` until validated canonical clips
  make the existing atomic `FLOW_READY` transition. If any lifecycle run is not
  healthy, stop with the sanitized evidence; do not stack another patch or make
  another paid request.
