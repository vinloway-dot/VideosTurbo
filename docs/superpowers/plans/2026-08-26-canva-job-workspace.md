# Canva Job Workspace Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Persist one Canva editor workspace per CloudJob so retries resume the same design and collect sanitized Audio-card evidence before acting.

**Architecture:** `CloudJobStore` persists an editor URL and last scoped Audio-card count. `CloudAgentWorkflow` persists the resolved URL before mutable Canva work; `CanvaAssemblyClient` resolves a create URL once and resumes by the stored editor URL.

**Tech Stack:** Python 3.11, Pydantic, SQLite, Playwright, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-26-canva-job-workspace-design.md`

## Global Constraints

- No new TTS, Flow generation, or retry operations.
- One Canva design per CloudJob; retries reuse its persisted editor URL.
- No unrelated Canva media deletion, coordinates, secrets, or browser-profile data.
- Every live failure retains `FLOW_READY` and source artifacts.

## Superseding validated Audio deletion selector (2026-08-26)

The managed narration cleanup contract is now proven in the live Canva UI. Audio
uses a generic `Show details` button that must be associated with the hovered
`voice.mp3` card through fresh overlapping, hit-tested geometry. Its popup Delete
command is `button[aria-label="Delete"]`, not a role/name `Delete` selector and
not Video's `Move to Trash`. Wait for that command to be visible, click it once,
re-open the live Audio panel, and require the managed-card count to decrease by
one. After cleanup, reload once and require the hydrated count remains zero; an
initial zero must first survive the bounded Audio-card hydration window.

---

### Task 1: Durable CloudJob workspace fields

**Files:** `app/models/cloud_agent.py`, `app/services/cloud_agent/job_store.py`, and `test/services/cloud_agent/test_job_store.py`.

**Produces:** `CloudJobRecord.canva_design_url: str`, `CloudJobRecord.canva_audio_card_count: int`, compatible SQLite migration, and mutable store fields.

- [ ] Write RED test:

```python
def test_cloud_job_store_round_trips_canva_workspace_fields(tmp_path):
    store = CloudJobStore(str(tmp_path / "cloud.sqlite3"))
    job = store.create_job(_request())
    updated = store.patch_job(
        job.id,
        canva_design_url="https://www.canva.com/design/DEMO/edit",
        canva_audio_card_count=1,
    )
    assert updated.canva_design_url.endswith("/design/DEMO/edit")
    assert store.get_job(job.id).canva_audio_card_count == 1
```

- [ ] Run RED: `uv run pytest test/services/cloud_agent/test_job_store.py -k canva_workspace -v`; expected failure: fields are absent.
- [ ] Add `canva_design_url TEXT NOT NULL DEFAULT ''` and `canva_audio_card_count INTEGER NOT NULL DEFAULT -1` to creation schema, compatible migration, Pydantic record, row/insert mappings, and mutable fields.
- [ ] Run GREEN: same command passes.
- [ ] Commit: `git commit -m "feat: persist Canva workspace state per job"`.

### Task 2: Resolve one editor URL and reuse it on resume

**Files:** `app/services/cloud_agent/providers/canva.py`, `app/services/cloud_agent/workflow.py`, `test/services/cloud_agent/test_canva.py`, and `test/services/cloud_agent/test_workflow.py`.

**Consumes:** Task 1 fields.

**Produces:** `CanvaAssemblyClient.open_job_session(job)` yields a session exposing `editor_url`; workflow persists it before `assemble_and_export`.

- [ ] Write RED tests:

```python
def test_first_canva_open_resolves_an_editor_url_not_the_create_url():
    with client.open_job_session(job) as session:
        assert session.editor_url == "https://www.canva.com/design/DEMO/edit"

def test_resume_opens_persisted_editor_url_without_create_url():
    job = store.patch_job(created.id, canva_design_url=EDITOR_URL)
    workflow.run(job.id, worker_id=WORKER_ID)
    assert canva.opened_urls == [EDITOR_URL]
```

- [ ] Run RED: `uv run pytest test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py -k 'editor_url or persisted_editor' -v`; expected failure: session accepts only `job_id` and no resolved URL is stored.
- [ ] Implement: select `job.canva_design_url` when non-empty; otherwise open configured create URL once; after ready, require an HTTPS Canva `/design/<id>/edit` URL that differs from the create URL; workflow persists it before cleanup/upload.
- [ ] Run GREEN: same command passes.
- [ ] Commit: `git commit -m "feat: resume each cloud job in its Canva workspace"`.

### Task 3: Scoped Audio evidence

**Files:** `app/services/cloud_agent/providers/canva.py`, `app/services/cloud_agent/workflow.py`, `test/services/cloud_agent/test_canva.py`, and `test/services/cloud_agent/test_workflow.py`.

**Consumes:** Task 1 count field and Task 2 persistent session.

**Produces:** integer-only Audio-card evidence persisted on job; typed fail-closed Audio verification.

- [ ] Write RED tests:

```python
@pytest.mark.parametrize("count", [0, 2])
def test_audio_card_count_not_one_fails_with_sanitized_count(count):
    with pytest.raises(CanvaUIVerificationError, match=rf"audio cards: {count}"):
        client._add_uploaded_audio(page_with_panel_count(count), "voice.mp3")

def test_workflow_persists_audio_count_from_canva_verification_failure():
    workflow.run(job.id, worker_id=WORKER_ID)
    assert store.get_job(job.id).canva_audio_card_count == 0
```

- [ ] Run RED: `uv run pytest test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py -k audio_card_count -v`; expected failure: no structured count reaches the store.
- [ ] Implement: count exact card controls only in active Uploads -> Audio panel; attach integer `audio_card_count` to typed error for zero or duplicates; workflow persists only the count and retains checkpoint/artifacts.
- [ ] Run GREEN: same command passes.
- [ ] Commit: `git commit -m "fix: persist scoped Canva audio verification evidence"`.

### Task 4: Full verification and one controlled resume

**Files:** no changes unless a RED test proves a new live discrepancy.

- [ ] Run full regression: `uv run pytest test/services/cloud_agent -q`.
- [ ] Run Ruff on model, store, workflow, provider, and changed tests; run `git diff --check`.
- [ ] Push branch and verify `origin/feature/cloud-video-agent` contains local HEAD.
- [ ] Confirm there are no unrelated claimable jobs. Resume only `7c76329b-c533-453d-8b2e-9533c2642153`; prove zero TTS/Flow calls and URL persistence before media mutation.
- [ ] If Audio fails, stop at its first new boundary, preserve `FLOW_READY`, and retain numeric count. Any new code needs a new RED test.
