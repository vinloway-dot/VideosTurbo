# Canva Workspace Cleanup and Six-Clip Assembly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the configured VideosTurbo Canva workspace self-cleaning before each assembly and after a durably validated final artifact, while adding the six canonical clips to the timeline in semantic order with observable proof.

**Architecture:** `CanvaAssemblyClient` owns browser-scoped editor operations: opening the configured design, proving an empty Uploads → Videos surface, proving an empty video timeline, uploading the canonical assets, and adding the six uniquely named cards. `CloudAgentWorkflow` remains the owner of durable checkpoints: it calls a separate Canva post-clean operation only after it has persisted `FINAL_VALIDATED`, so a cleanup failure cannot invalidate the final MP4.

**Tech Stack:** Python 3.11, Playwright sync API with headed Chrome/persistent Canva profile, pytest, Ruff, existing Cloud Agent SQLite checkpoint workflow.

**Spec:** `/home/linuxuser/.codex/attachments/e8608d2d-9622-47db-b8da-8221261b6a99/VideosTurbo_Codex_Handoff_Canva_Cleanup.md`

## Global Constraints

- Use the configured `cloud_agent_canva_template_url`; never navigate to another Canva project.
- Cleanup applies only to `Uploads → Videos`; never delete Images or Audio.
- The production Canva workspace is dedicated to VideosTurbo. If a live inspection proves unrelated user videos are present, fail closed before broad deletion.
- Never use fixed coordinates, `force=True`, cached locators, or cached geometry.
- The only permitted geometric clicks are fresh, card-scoped hit-tested coordinates for Canva's transient details overlay and its immediately opened `Move to Trash` menu item.
- Verify the card menu contains `Details`, `Download`, `Move`, and `Move to Trash` before any deletion.
- Re-query the current gallery after every delete. A missing Videos tab is a verified zero-video state.
- Do not synthesize TTS, submit Google Flow Generate, resize/transcode source Flow clips, start Task 15, or consume Paid Attempt #2.
- Preserve the existing final MP4/checkpoint if post-clean fails. No new schema flag is introduced unless a failing recovery test proves it necessary.
- All production behavior begins with a focused RED test whose failure is caused by the missing behavior, then the smallest GREEN implementation.

---

### Task 1: Record the exact cleanup/assembly contract in tests

**Files:**
- Modify: `test/services/cloud_agent/test_canva.py`
- Modify: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**
- Produces fake page doubles which expose `clean_uploaded_videos()`, `clear_video_timeline()`, `add_uploaded_clip(name)`, and counted observable states.
- Produces test coverage for a public `CanvaAssemblyClient.clean_workspace(job_id: str) -> None` and the updated `assemble_and_export(...)` sequence.

- [ ] **Step 1: Write failing Canva pre-clean tests**

Add a fake editor with a mutable upload-video inventory and action log. Test that `assemble_and_export` emits these operations in order:

```python
("clean_uploaded_videos",)
("clear_video_timeline",)
("upload", ("clip_01.mp4", ..., "clip_06.mp4", "voice.mp3"))
```

Add individual tests asserting that `_clean_uploaded_videos(page)`:

```python
# zero state is accepted when the Videos tab is absent
client._clean_uploaded_videos(FakeNoVideosTabPage())

# every deletion receives a new gallery snapshot
assert page.gallery_queries == page.deleted_cards + 1

# deletion is rejected when the media-card menu lacks Move to Trash
with pytest.raises(canva.CanvaUIVerificationError, match="media-card menu"):
    client._clean_uploaded_videos(FakeWrongMenuPage())
```

The geometry fake must assert that the selected overlay belongs to the currently hovered card and that its `(x, y)` is read freshly for each deletion; it must not expose an API for a fixed coordinate or `.first` ownership.

- [ ] **Step 2: Run focused RED tests**

Run:

```bash
uv run pytest test/services/cloud_agent/test_canva.py -k 'clean or timeline_add' -v
```

Expected: failures identify missing `_clean_uploaded_videos`, `_clear_video_timeline`, and `_add_uploaded_clips`, not fixture/import errors.

- [ ] **Step 3: Write failing six-clip insertion tests**

Use a fake page whose `timeline_video_count` starts at zero. Assert `_add_uploaded_clips(page, expected_names)` clicks exactly the semantic order and proves each precise transition:

```python
assert page.added == [
    ("clip_01.mp4", 0, 1),
    ("clip_02.mp4", 1, 2),
    ("clip_03.mp4", 2, 3),
    ("clip_04.mp4", 3, 4),
    ("clip_05.mp4", 4, 5),
    ("clip_06.mp4", 5, 6),
]
```

Add a failure case where one card click does not increase the count by exactly one and assert `CanvaUIVerificationError` is raised before another card is clicked.

- [ ] **Step 4: Run the insertion RED tests**

Run:

```bash
uv run pytest test/services/cloud_agent/test_canva.py -k 'timeline_add' -v
```

Expected: fail because no timeline-add implementation exists.

- [ ] **Step 5: Write failing post-validation cleanup workflow tests**

Extend the workflow Canva fake with `clean_workspace(job_id)`. Add two focused tests:

```python
def test_workflow_post_cleans_canva_only_after_final_validated(...):
    result = workflow.run(job.id)
    assert events.index("final_validated") < events.index("canva_post_clean")
    assert result.checkpoint is CloudJobCheckpoint.COMPLETED

def test_workflow_keeps_final_validated_artifact_when_canva_post_clean_fails(...):
    result = workflow.run(job.id)
    assert result.checkpoint is CloudJobCheckpoint.COMPLETED
    assert final_file.is_file()
    assert "canva_post_clean" in canva.events
```

The latter test must make `clean_workspace` raise and verify no TTS or Flow fake method is called.

- [ ] **Step 6: Run workflow RED tests**

Run:

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -k 'canva_post_clean' -v
```

Expected: fail because the workflow currently completes directly after `FINAL_VALIDATED`.

---

### Task 2: Implement fail-closed Canva workspace pre-clean and timeline preparation

**Files:**
- Modify: `app/services/cloud_agent/providers/canva.py`
- Test: `test/services/cloud_agent/test_canva.py`

**Interfaces:**
- Produces `CanvaAssemblyClient._clean_uploaded_videos(page: Any) -> None`.
- Produces `CanvaAssemblyClient._clear_video_timeline(page: Any) -> None`.
- Produces `CanvaAssemblyClient._add_uploaded_clips(page: Any, expected_names: list[str]) -> None`.
- `assemble_and_export` calls these methods after `page.goto(...)` and before `_upload_media(...)`.

- [ ] **Step 1: Add only the helpers required by the RED tests**

Implement `_clean_uploaded_videos` with this observable sequence:

```python
open Uploads
if Videos tab absent: return
click Videos tab
while current video cards exist:
    choose one current visible card
    hover it
    choose a visible `Show details for “<name>”` overlay only if its fresh box
    overlaps the card and elementFromPoint proves it is hit-testable
    click the fresh overlay centre
    require exactly one visible `Move to Trash`
    require the same open menu also exposes Details, Download, and Move
    hit-test and click the fresh Trash centre immediately
    wait for gallery mutation, then obtain a wholly new snapshot
reopen Uploads → Videos and require zero cards, or a missing Videos tab
```

Any unavailable tab, ambiguous overlay/menu, failed hit test, unchanged gallery, or non-zero final count raises `CanvaUIVerificationError`. Do not catch this into a success.

Implement `_clear_video_timeline` by selecting each current video timeline parent and pressing `Delete`, re-reading `_VIDEO_START_EDGE` after each delete, and requiring its final count to be zero. It must not select narration/audio-only items.

Implement `_add_uploaded_clips` by requiring exactly one accessible button named each `clip_01.mp4` through `clip_06.mp4` after the pre-clean/upload path. For each name, record `_VIDEO_START_EDGE` count before the click, click that unique card, wait boundedly for the count, and require `after == before + 1`.

- [ ] **Step 2: Wire preparation into assembly**

Change the assembly sequence to:

```python
page.goto(self.service_url, wait_until="domcontentloaded")
self._clean_uploaded_videos(page)
self._clear_video_timeline(page)
self._upload_media(page, [*clip_paths, audio_path])
self._add_uploaded_clips(page, [clip.name for clip in clip_paths])
self._order_clips(page, [clip.name for clip in clip_paths])
```

Do not change the existing playback, mute, narration, trim, captions, export, or download behavior in this task.

- [ ] **Step 3: Run focused GREEN tests**

Run:

```bash
uv run pytest test/services/cloud_agent/test_canva.py -k 'clean or timeline_add or assembly' -v
```

Expected: all focused tests pass.

- [ ] **Step 4: Run the full Canva provider test file**

Run:

```bash
uv run pytest test/services/cloud_agent/test_canva.py -v
```

Expected: pass with no changed legacy rendering/stock behavior.

---

### Task 3: Implement post-validation Canva cleanup without weakening durability

**Files:**
- Modify: `app/services/cloud_agent/providers/canva.py`
- Modify: `app/services/cloud_agent/workflow.py`
- Modify: `test/services/cloud_agent/test_workflow.py`

**Interfaces:**
- Produces `CanvaAssemblyClient.clean_workspace(job_id: str) -> None`, which enters the configured design under the existing `canva` browser-profile lock and calls `_clean_uploaded_videos`.
- `CloudAgentWorkflow.run` calls `self.canva.clean_workspace(job.id)` after atomically persisting `FINAL_VALIDATED` and before persisting `COMPLETED`.

- [ ] **Step 1: Implement the public non-checkpointing cleanup client method**

`clean_workspace` must:

```python
self.sessions.ensure_service_ready("canva", job_id)
with self.browser.open("canva", headed=True) as context:
    page = BrowserSessionProvider._page(context)
    page.goto(self.service_url, wait_until="domcontentloaded")
    self._clean_uploaded_videos(page)
```

It must not inspect or write SQLite, interact with Flow, remove Audio/Images, or publish browser/profile paths.

- [ ] **Step 2: Implement the workflow durability boundary**

After `FINAL_VALIDATED` is persisted, call the cleanup in a narrow `try/except Exception` that logs only the job id and exception type. Continue to the existing `COMPLETED` transition even when cleanup fails; preserve `final_video`, `FINAL_VALIDATED`, and the local artifact. Do not retry TTS/Flow and do not add a DB flag.

- [ ] **Step 3: Run post-clean focused GREEN tests**

Run:

```bash
uv run pytest test/services/cloud_agent/test_workflow.py -k 'canva_post_clean' -v
```

Expected: post-clean is ordered after durable final validation; a post-clean exception leaves a completed job with its final artifact and no paid-stage calls.

- [ ] **Step 4: Run Cloud Agent regression**

Run:

```bash
uv run pytest test/services/cloud_agent/test_workflow.py test/services/cloud_agent/test_canva.py -v
```

Expected: pass.

---

### Task 4: Static validation, deployment safety review, and live resume gate

**Files:**
- Modify: `docs/superpowers/plans/2026-08-25-canva-workspace-cleanup.md` (check boxes/evidence only)

- [ ] **Step 1: Run required non-mutating verification**

Run:

```bash
uv lock --check
uv sync --frozen
uv run python -m compileall app cli.py main.py webui test
uv run ruff check app/services/cloud_agent/providers/canva.py app/services/cloud_agent/workflow.py test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py
uv run pytest
git diff --check
```

Confirm the full suite preserves the repository coverage floor of 70% or higher.

- [ ] **Step 2: Commit and push the bounded change**

Run:

```bash
git add app/services/cloud_agent/providers/canva.py app/services/cloud_agent/workflow.py test/services/cloud_agent/test_canva.py test/services/cloud_agent/test_workflow.py docs/superpowers/plans/2026-08-25-canva-workspace-cleanup.md
git commit -m "feat: clean Canva workspace around cloud assembly"
git push origin feature/cloud-video-agent
git rev-parse HEAD
git rev-parse origin/feature/cloud-video-agent
```

Require local and remote SHA equality, then wait for Windows, Python 3.11, and Python 3.13 CI success. Do not mark PR #4 ready.

- [ ] **Step 3: Verify the live resume preconditions without paid work**

Read the existing job `7c76329b-c533-453d-8b2e-9533c2642153` and verify:

```text
checkpoint == FLOW_READY
flow_generation_unresolved == false
canonical voice.mp3 exists and validates
clip_01.mp4 … clip_06.mp4 exist and validate
Worker is inactive before controlled resume
```

If Canva requires a login, CAPTCHA, 2FA, device confirmation, OAuth, or account selection, stop with `HUMAN_REQUIRED_AUTH`. If any source artifact is missing/invalid, stop without new TTS/Flow work.

- [ ] **Step 4: Resume only after CI and preconditions pass**

Resume the same job from `FLOW_READY`. The expected non-paid path is:

```text
Canva pre-clean → timeline zero → upload existing 6 clips + existing voice
→ add clips 01..06 with exact count transitions → existing Canva assembly
→ server final validation → durable FINAL_VALIDATED → Canva post-clean → COMPLETED
```

If any Canva selector/action cannot be observably verified, stop at the durable checkpoint. Never start TTS, Google Flow, Paid Attempt #2, or Task 15.

## Plan Self-Review

- Spec coverage: Tasks 1–2 cover pre-clean, timeline clearing, upload isolation, semantic insertion and observable transitions. Task 3 covers post-clean after `FINAL_VALIDATED` and failure durability. Task 4 covers all required verification, CI, and the bounded live resume gate.
- Placeholder scan: no TBD/TODO placeholders; each task names exact production/test files, interfaces, commands, and expected RED/GREEN outcome.
- Type consistency: `clean_workspace(job_id: str)` is defined in Task 3 and consumed only there; all private helpers use `page: Any` consistently with the existing adapter.
