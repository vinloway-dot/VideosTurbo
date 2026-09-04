# Idempotent Google Flow Agent Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Before every production-code behavior change, use `superpowers:test-driven-development`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make fresh Google Flow generation fail closed unless a verified active Agent composer owns the paid prompt and Generate control.

**Architecture:** `GoogleFlowClient` owns observable Agent-state and composer-ownership checks under the existing Flow workspace/profile lock. `CloudAgentWorkflow` continues to own the durable paid-generation fence and containment of verification errors; no provider writes SQLite. The existing fenced reconciliation path remains isolated from fresh Agent preparation.

**Tech Stack:** Python 3.11+, Playwright sync API, pytest, Ruff, existing CloudJob SQLite/workflow contracts.

**Spec:** `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md` §3.11.

## Global Constraints

- Keep `videosturbo-worker.service` inactive during all implementation and verification.
- Never call TTS, Google Flow Generate, rename, download, delete, or pre-clean remote Flow assets.
- Preserve job `c604f5d5-c206-4d49-bad2-cac59e2815a2` as `HUMAN_REQUIRED / TTS_READY / flow_generation_unresolved=true`; do not modify its audio or remote evidence.
- Use existing authenticated Flow profile only for non-paid selector verification; do not log credentials or session data.
- Unknown Agent state is fail-closed: zero Agent clicks, prompt fills, and Generate clicks.
- Preserve the durable paid fence and reconciliation/cleanup/archive behavior.

---

### Task 1: Specify and RED-test the Agent activation boundary

**Files:**
- Modify: `app/services/cloud_agent/providers/google_flow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class AgentComposer:
    agent: Any
    container: Any
    prompt: Any
    generate: Any

def _ensure_agent_active(self, page: Any) -> AgentComposer: ...
def _active_agent_composer(self, page: Any) -> AgentComposer: ...
```

- [x] Add RED fake-page tests for inactive → exactly one click → active composer.
- [x] Add RED tests for already-active → zero clicks and two calls remain active.
- [x] Add RED tests for unknown state, delayed activation, and never-active timeout: no fill/Generate.
- [x] Add RED tests where default Image composer remains visible or multiple prompt-like fields exist; only the verified Agent container field is eligible.
- [x] Add RED tests for active-at-fill then inactive-before-Generate: abort before click.
- [x] Run focused tests and record that failures are missing Agent activation/ownership behavior, not fixture/import errors.
- [x] Implement only `AgentComposer` and fail-closed active-state/composer observation. Treat only exact `aria-pressed="true"` and `"false"` as known states; poll the existing bounded editor timeout after one inactive click.
- [x] Replace global prompt lookup in `_submit_agent_prompt` with: ensure active → verified container prompt → state/ownership re-check → fill → prompt-value verification → final state/ownership re-check → Generate.
- [x] Run the focused activation tests after each behavior and then the complete Google Flow adapter test module.

### Task 2: Preserve workflow fence and resume safety

**Files:**
- Modify: `app/services/cloud_agent/workflow.py` only if a failing Task 2 test proves an ordering/containment gap.
- Modify: `test/services/cloud_agent/test_workflow.py`
- Modify: `test/services/cloud_agent/test_google_flow.py`

- [x] Add RED workflow tests proving `flow_generation_unresolved=true` is durable before provider Generate, failures before the fence do not create it, and a failure after it remains conservative.
- [x] Add RED test that Agent activation `FlowWorkspaceVerificationError` is contained as `HUMAN_REQUIRED` without terminating the worker.
- [x] Add RED regression that `TTS_READY + flow_generation_unresolved=true` never enters fresh Agent activation, pre-clean, or Generate; `FLOW_READY` restart remains non-generating.
- [x] Run the focused workflow RED tests and confirm failures identify the intended missing ordering/containment behavior.
- [x] Make the smallest workflow correction only if RED demonstrates one is needed; otherwise leave existing v2.4 fence code unchanged.
- [x] Run focused workflow and Flow regression tests through GREEN.

### Task 3: Documentation and live non-paid verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-cloud-video-agent-design.md`
- Create: `docs/superpowers/plans/2026-08-24-flow-agent-activation.md`

- [x] Document the verified `aria-pressed` contract, persistence, fail-closed ambiguity rule, verified composer ownership, and fence order.
- [x] With the implementation in place, use one headed persistent Flow context to prove: inactive → ensure active, active → ensure has no toggle click, verified Agent prompt/composer ownership, and final active state. Do not fill or Generate.
- [x] Recheck that the protected job and its two remote image assets remain unchanged and the worker is inactive.

### Task 4: Full verification, commit, push, and CI

**Files:** all Task 1–3 files only.

- [x] Run `uv lock --check` and `uv sync --frozen`.
- [x] Run `uv run python -m compileall app webui`.
- [x] Run `uv run ruff check app webui test`.
- [x] Run `uv run pytest` and verify coverage remains at least 70%.
- [ ] Run `git diff --check`, inspect the diff, commit `fix: make flow agent activation idempotent`, push `feature/cloud-video-agent`, and verify local/remote SHA match.
- [ ] Wait for and inspect GitHub Windows smoke, Python 3.11, and Python 3.13 checks. Do not begin Task 15 or request another paid E2E.
