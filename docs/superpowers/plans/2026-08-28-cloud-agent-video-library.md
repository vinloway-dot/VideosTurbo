# Cloud Agent Video Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automatically refreshed, paginated 5 × 2 library of completed Cloud Agent videos, with safe permanent deletion from VideosTurbo cloud storage.

**Architecture:** CloudVideoLibraryService combines the existing SQLite job store and job-scoped storage to decide which jobs are visible and to delete them safely. Two dedicated FastAPI routes expose public video data and deletion. Streamlit renders the library between Production status and Job controls, using the existing final-video route for playback.

**Tech Stack:** FastAPI, Pydantic, SQLite, pathlib/shutil, Streamlit, project CSS, pytest, Playwright.

**Spec:** docs/superpowers/specs/2026-08-28-cloud-agent-video-library-design.md

## Global Constraints

- Page size is exactly 10 videos in a 5 × 2 desktop grid; use numbered pagination for additional results.
- A visible item is COMPLETED, has a final checkpoint, and has a validated final video file.
- Sort by completed_at DESC, id DESC so newly completed videos appear first.
- Require confirmation; delete only the chosen local VideosTurbo artifacts and Cloud Agent record.
- Never contact/delete Google Flow or Canva data; do not alter workflow, worker, provider, session, or credit behavior.
- Never expose local file paths in API or WebUI. Use TDD and make a focused commit after each task.

---

## File Structure

- Create app/services/cloud_agent/video_library.py: eligibility, pagination, and deletion coordinator.
- Modify app/services/cloud_agent/job_store.py: completed-final query and exact record deletion.
- Modify app/services/cloud_agent/storage.py: final-file check and safe job-directory removal.
- Modify app/controllers/v1/cloud_agent.py: GET /cloud-agent/videos and DELETE /cloud-agent/videos/{job_id}.
- Create test/services/test_cloud_agent_video_library.py: service, ordering, boundary, and deletion tests.
- Modify test/services/test_cloud_agent_controller.py: API route tests.
- Modify webui/cloud_agent_ui.py, webui/cloud_agent.py, and webui/cloud_agent.css: library renderer, page state, confirmation, and layout.
- Modify test/services/test_cloud_agent_ui.py and test/services/test_cloud_agent_webui.py: UI tests.

### Task 1: Job store and storage primitives

**Files:**
- Modify: app/services/cloud_agent/job_store.py
- Modify: app/services/cloud_agent/storage.py
- Create: test/services/test_cloud_agent_video_library.py

**Interfaces:**
- Produces CloudJobStore.list_completed_final_candidates() -> list[CloudJobRecord] and CloudJobStore.delete_job(job_id: str) -> None.
- Produces CloudJobStorage.has_valid_final_video(job_id: str, recorded_final_video: str) -> bool, stage_job_artifacts(job_id: str) -> Path, restore_staged_job(job_id: str, staged_dir: Path) -> None, and purge_staged_job(staged_dir: Path) -> None.

- [ ] **Step 1: Write the failing tests**

~~~python
def test_completed_final_candidates_are_sorted_by_completion_then_id(tmp_path):
    store = CloudJobStore(str(tmp_path / "jobs.sqlite3"))
    older = _completed_job(store, job_id="a", completed_at="2026-08-28T10:00:00+00:00")
    newer = _completed_job(store, job_id="b", completed_at="2026-08-28T11:00:00+00:00")
    _queued_job(store)
    assert [job.id for job in store.list_completed_final_candidates()] == [newer.id, older.id]

def test_stage_job_artifacts_moves_only_its_job_and_rejects_escape(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    target = storage.prepare("job-a")
    sibling = storage.prepare("job-b")
    target.final_file.write_bytes(b"mp4")
    sibling.final_file.write_bytes(b"mp4")
    staged = storage.stage_job_artifacts("job-a")
    assert not target.job_dir.exists()
    assert staged.is_dir()
    assert sibling.final_file.exists()
    with pytest.raises(ValueError):
        storage.stage_job_artifacts("../outside")
~~~

- [ ] **Step 2: Run tests to verify failure**

Run: pytest test/services/test_cloud_agent_video_library.py -v

Expected: FAIL because the candidate-query and artifact-staging methods do not exist.

- [ ] **Step 3: Implement the minimal store query and deletion**

~~~python
def list_completed_final_candidates(self) -> list[CloudJobRecord]:
    with self._connect() as connection:
        rows = connection.execute(
            """SELECT * FROM cloud_agent_jobs
               WHERE status = ? AND checkpoint IN (?, ?)
               ORDER BY completed_at DESC, id DESC""",
            (CloudJobStatus.COMPLETED.value,
             CloudJobCheckpoint.FINAL_VALIDATED.value,
             CloudJobCheckpoint.COMPLETED.value),
        ).fetchall()
    return [self._row_to_record(row) for row in rows]

def delete_job(self, job_id: str) -> None:
    with self._connect() as connection:
        cursor = connection.execute("DELETE FROM cloud_agent_jobs WHERE id = ?", (job_id,))
        if cursor.rowcount != 1:
            raise KeyError(job_id)
~~~

- [ ] **Step 4: Implement safe storage methods**

Use _paths(job_id) to derive the trusted directory. has_valid_final_video must compare recorded_final_video to paths.final_file.resolve() and use resolve_path_within_directory(..., require_file=True). stage_job_artifacts must not call prepare(), must require paths.job_dir.parent == self.root.resolve(), and must rename the job directory to a unique child of root/.deleting. restore_staged_job must validate both paths before renaming the staged directory back. purge_staged_job may call shutil.rmtree only after confirming its resolved parent is the .deleting directory below storage root.

- [ ] **Step 5: Verify and commit**

Run: pytest test/services/test_cloud_agent_video_library.py -v && ruff check app/services/cloud_agent/job_store.py app/services/cloud_agent/storage.py test/services/test_cloud_agent_video_library.py

Expected: PASS with zero Ruff diagnostics.

~~~bash
git add app/services/cloud_agent/job_store.py app/services/cloud_agent/storage.py test/services/test_cloud_agent_video_library.py
git commit -m "feat: add cloud video library storage primitives"
~~~

### Task 2: Library service and API contract

**Files:**
- Create: app/services/cloud_agent/video_library.py
- Modify: app/controllers/v1/cloud_agent.py
- Modify: test/services/test_cloud_agent_video_library.py
- Modify: test/services/test_cloud_agent_controller.py

**Interfaces:**
- Produces VideoLibraryItem(job_id: str, subject: str, completed_at: str) and VideoLibraryPage(items: tuple[VideoLibraryItem, ...], page: int, page_size: int, total_items: int, total_pages: int).
- Produces CloudVideoLibraryService.list_videos(page: int, page_size: int) -> VideoLibraryPage and delete_video(job_id: str) -> None.
- Adds GET /api/v1/cloud-agent/videos?page=1&page_size=10 and DELETE /api/v1/cloud-agent/videos/{job_id}.

- [ ] **Step 1: Write failing service and API tests**

~~~python
def test_library_filters_missing_final_files_before_pagination(tmp_path):
    service, store, storage = _library_service(tmp_path)
    visible = _completed_job(store, job_id="visible", completed_at="2026-08-28T12:00:00+00:00")
    _write_final(storage, visible)
    _completed_job(store, job_id="missing", completed_at="2026-08-28T13:00:00+00:00")
    page = service.list_videos(page=1, page_size=10)
    assert [item.job_id for item in page.items] == [visible.id]
    assert (page.total_items, page.total_pages) == (1, 1)

def test_video_library_api_exposes_no_local_path(tmp_path):
    client, store = _client(tmp_path)
    job = _created_completed_final_job(client, store, tmp_path)
    response = client.get("/api/v1/cloud-agent/videos?page=1&page_size=10")
    assert response.status_code == 200
    assert response.json()["data"]["items"][0] == {
        "job_id": job.id, "subject": job.subject, "completed_at": job.completed_at,
        "final_url": f"/api/v1/cloud-agent/jobs/{job.id}/final",
    }
    assert str(tmp_path) not in response.text
~~~

- [ ] **Step 2: Run tests to verify failure**

Run: pytest test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_controller.py -k 'library or video_delete' -v

Expected: FAIL because the service and routes do not exist.

- [ ] **Step 3: Implement CloudVideoLibraryService**

~~~python
def list_videos(self, *, page: int, page_size: int) -> VideoLibraryPage:
    if page < 1 or page_size != 10:
        raise ValueError("invalid video library page")
    visible = tuple(
        VideoLibraryItem.from_job(job)
        for job in self._store.list_completed_final_candidates()
        if self._storage.has_valid_final_video(job.id, job.final_video)
    )
    return VideoLibraryPage.from_items(visible, page=page, page_size=page_size)

def delete_video(self, job_id: str) -> None:
    job = self._store.get_job(job_id)
    if job is None or not self._is_visible(job):
        raise VideoLibraryNotFoundError(job_id)
    staged = self._storage.stage_job_artifacts(job.id)
    try:
        self._store.delete_job(job.id)
    except Exception:
        self._storage.restore_staged_job(job.id, staged)
        raise
    self._storage.purge_staged_job(staged)
~~~

VideoLibraryPage.from_items must calculate max(1, ceil(total_items / page_size)), return an empty tuple for out-of-range pages, and never contain final_video.

- [ ] **Step 4: Add controller routes and expected-error mapping**

Add a dependency factory for CloudVideoLibraryService. Enforce page >= 1 and page_size == 10. Map missing/non-visible jobs to 404 without disclosing job details. Wrap every response in utils.get_response; build final_url from the existing final route and update EXPECTED_CLOUD_AGENT_PATHS in the controller test.

- [ ] **Step 5: Verify and commit**

Run: pytest test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_controller.py -v && ruff check app/services/cloud_agent/video_library.py app/controllers/v1/cloud_agent.py test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_controller.py

Expected: PASS with both API routes in the router contract.

~~~bash
git add app/services/cloud_agent/video_library.py app/controllers/v1/cloud_agent.py test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_controller.py
git commit -m "feat: expose cloud agent video library api"
~~~

### Task 3: Library card UI and layout

**Files:**
- Modify: webui/cloud_agent_ui.py
- Modify: webui/cloud_agent.css
- Modify: test/services/test_cloud_agent_ui.py

**Interfaces:**
- Produces VideoCardView(job_id: str, subject: str, completed_at: str, final_url: str) and VideoLibraryView(items: tuple[VideoCardView, ...], page: int, total_pages: int, total_items: int).
- Produces video_library_view(payload: Mapping[str, object]) -> VideoLibraryView and render_video_library(view, *, on_delete, on_page) -> None.

- [ ] **Step 1: Write failing view-model and structure tests**

~~~python
def test_video_library_view_keeps_only_public_card_fields():
    view = cloud_agent_ui.video_library_view({
        "items": [{"job_id": "job-1", "subject": "Newest", "completed_at": "2026-08-28T12:00:00+00:00", "final_url": "/api/v1/cloud-agent/jobs/job-1/final"}],
        "page": 1, "total_pages": 3, "total_items": 21,
    })
    assert (view.page, view.total_pages, view.items[0].job_id) == (1, 3, "job-1")
    assert not hasattr(view.items[0], "final_video")

def test_video_library_css_declares_a_five_column_desktop_grid():
    assert ".vt-video-library-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }" in Path("webui/cloud_agent.css").read_text()
~~~

- [ ] **Step 2: Run tests to verify failure**

Run: pytest test/services/test_cloud_agent_ui.py -k 'video_library' -v

Expected: FAIL because the types, converter, renderer, and CSS do not exist.

- [ ] **Step 3: Implement reusable renderer and CSS**

Render a วีดีโอที่สร้าง section with a 5-column card grid. Each card uses st.video(final_url), escaped subject/time, and a key containing the job ID. Show a Thai neutral empty state for no items. Page buttons run from 1 through total_pages, with the current page disabled. CSS must be five columns on desktop, three on tablet, two on mobile, one on narrow mobile; preserve white surfaces and a visibly destructive red delete control.

- [ ] **Step 4: Verify and commit**

Run: pytest test/services/test_cloud_agent_ui.py -v && ruff check webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py

Expected: PASS with zero Ruff diagnostics.

~~~bash
git add webui/cloud_agent_ui.py webui/cloud_agent.css test/services/test_cloud_agent_ui.py
git commit -m "feat: render cloud agent video library cards"
~~~

### Task 4: Page integration, automatic refresh, and confirmation

**Files:**
- Modify: webui/cloud_agent.py
- Modify: test/services/test_cloud_agent_webui.py

**Interfaces:**
- Produces _load_video_library(page: int) -> dict, _delete_video(job_id: str) -> None, and _render_video_library(*, ui_state: MutableMapping) -> None.
- Uses session keys cloud_agent_video_library_page and cloud_agent_video_delete_pending_id.

- [ ] **Step 1: Write failing interaction tests**

~~~python
def test_video_library_requests_ten_items_and_renders_before_job_controls(monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_agent, "_api", lambda method, path, **kwargs: calls.append((method, path)) or _video_page())
    assert cloud_agent._load_video_library(1)["page_size"] == 10
    assert calls == [("GET", "videos?page=1&page_size=10")]
    source = Path("webui/cloud_agent.py").read_text()
    assert source.index("_render_video_library") < source.index('st.expander("Job controls"')

def test_successful_deletion_falls_back_to_previous_valid_page(monkeypatch):
    state = {"cloud_agent_video_library_page": 3, "cloud_agent_video_delete_pending_id": "job-9"}
    monkeypatch.setattr(cloud_agent, "_api", lambda method, path, **kwargs: {"total_pages": 2} if method == "GET" else {})
    cloud_agent._confirm_video_deletion(ui_state=state, job_id="job-9")
    assert state == {"cloud_agent_video_library_page": 2, "cloud_agent_video_delete_pending_id": ""}
~~~

- [ ] **Step 2: Run tests to verify failure**

Run: pytest test/services/test_cloud_agent_webui.py -k 'video_library' -v

Expected: FAIL because library API helpers and confirmation state do not exist.

- [ ] **Step 3: Implement API calls, confirmation, and placement**

Read with _api("GET", f"videos?page={page}&page_size=10"); delete only after confirmation using _api("DELETE", f"videos/{job_id}"). First delete click stores the ID and displays Thai permanent-deletion wording; cancel clears only that ID. Success reloads the page and falls back when it became empty. Route requests.RequestException through _api_error_message and keep the card if the call fails. Add the library slot immediately after production_status_slot and before the Job controls expander. Existing terminal-status app rerun must therefore fetch the new completed video with no manual action.

- [ ] **Step 4: Verify and commit**

Run: pytest test/services/test_cloud_agent_webui.py -v && ruff check webui/cloud_agent.py test/services/test_cloud_agent_webui.py

Expected: PASS with a source-order assertion proving the library precedes Job controls.

~~~bash
git add webui/cloud_agent.py test/services/test_cloud_agent_webui.py
git commit -m "feat: add cloud agent completed video library"
~~~

### Task 5: Full verification and operator smoke check

**Files:**
- Modify only files from Tasks 1–4 when a verification failure needs a scoped correction.

**Interfaces:**
- Consumes all API and UI interfaces from Tasks 1–4; produces verified behavior without worker/provider changes.

- [ ] **Step 1: Run complete Cloud Agent test coverage**

Run: pytest test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py test/services/test_cloud_agent_video_library.py -v

Expected: PASS with no failures.

- [ ] **Step 2: Run full regression and lint checks**

Run: pytest -q && ruff check app/controllers/v1/cloud_agent.py app/services/cloud_agent/job_store.py app/services/cloud_agent/storage.py app/services/cloud_agent/video_library.py webui/cloud_agent.py webui/cloud_agent_ui.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py && git diff --check

Expected: all tests pass, Ruff has zero diagnostics, and git diff --check has no output.

- [ ] **Step 3: Smoke check in WebUI**

1. Open Cloud Agent with a completed local job.
2. Confirm its video appears below Production status without entering a Job ID.
3. Confirm a desktop page has no more than 10 cards, newest completion first.
4. Navigate numbered pages; open and cancel deletion; verify the card remains.
5. Delete a disposable completed job; confirm its final route returns 404 and it disappears from the library.
6. Confirm neither Flow nor Canva browser state changes during library read/delete actions.

- [ ] **Step 4: Commit only if Task 5 required a scoped correction**

Run: git status --short

If verification produced no tracked correction, do not create a commit. If it did, add only corrected files from Tasks 1–4 and use commit message test: verify cloud agent video library.
