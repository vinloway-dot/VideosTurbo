# Completed Videos Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `วีดีโอที่สร้าง` sidebar page and remove completed-video rendering from the Cloud Agent production page without changing the production workflow.

**Architecture:** Extract the existing completed-video controller into `webui/completed_videos.py`, keep the existing UI renderer and backend API unchanged, and render the controller only from a new Streamlit page. Cloud Agent retains Production status and Job controls but no longer calls the video-list API.

**Tech Stack:** Python, Streamlit, requests, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-28-completed-videos-page-design.md`

## Global Constraints

- Do not change Worker, workflow, database, storage, Google Flow, Canva, export, provider, or API behavior.
- Keep exactly 10 videos per page in the existing 5 × 2 desktop layout.
- Fetch the list only while rendering the dedicated completed-videos page; add no polling.
- Keep media failures isolated to one card and list failures isolated to the dedicated page.
- Preserve confirmation and permanent local VideosTurbo deletion behavior.

---

### Task 1: Dedicated completed-video controller

**Files:**
- Create: `webui/completed_videos.py`
- Modify: `test/services/test_cloud_agent_webui.py`

**Interfaces:**
- Produces `load_video_library(page: int) -> dict`.
- Produces `load_video_media(final_url: str) -> bytes`.
- Produces `confirm_video_deletion(*, ui_state: MutableMapping, job_id: str) -> bool`.
- Produces `render_video_library(*, ui_state: MutableMapping) -> None`.

- [x] **Step 1: Write failing controller tests**

Retarget the existing video-library fixtures and interaction expectations in `test/services/test_cloud_agent_webui.py` to `webui.completed_videos`, and assert that page 1 requests the literal path `videos?page=1&page_size=10`. Remove the old library stub from Cloud Agent panel tests so any remaining video-list request fails those tests as an unexpected API call.

- [x] **Step 2: Run tests to verify failure**

Run: `pytest test/services/test_cloud_agent_webui.py -k 'video_library or completed_video or pause_refreshes_snapshot' -v`

Expected: FAIL because `webui.completed_videos` does not exist and Cloud Agent still renders the library.

- [x] **Step 3: Implement the controller extraction**

Create `webui/completed_videos.py` with the existing fixed-size list request, validated internal final-video fetch, deletion confirmation, pagination state, renderer callbacks, and page-local exception boundary. Remove those functions and the `video_library_slot` call from `webui/cloud_agent.py`.

- [x] **Step 4: Run focused tests**

Run: `pytest test/services/test_cloud_agent_webui.py -k 'video_library or completed_video or pause_refreshes_snapshot' -v`

Expected: PASS.

### Task 2: Sidebar navigation and page entry point

**Files:**
- Create: `webui/pages/4_Completed_Videos.py`
- Modify: `webui/cloud_agent_ui.py`
- Modify: `test/services/test_cloud_agent_ui.py`
- Create: `test/services/test_completed_videos_page.py`

**Interfaces:**
- Sidebar link target: `pages/4_Completed_Videos.py`.
- Sidebar label: `วีดีโอที่สร้าง`.
- Page calls `completed_videos.render_video_library(ui_state=st.session_state)`.

- [x] **Step 1: Write failing navigation and page tests**

Assert that `render_sidebar()` emits a page link with the exact target and label. Execute the new page entry renderer with Streamlit replaced by a test double and assert that it applies the shared theme/sidebar and passes the real session state to the completed-video controller.

- [x] **Step 2: Run tests to verify failure**

Run: `pytest test/services/test_cloud_agent_ui.py -k sidebar -v && pytest test/services/test_completed_videos_page.py -v`

Expected: FAIL because the link and page do not exist.

- [x] **Step 3: Implement the page and link**

Add the page link immediately below Cloud Agent. The new page sets wide layout, applies the shared theme, renders the shared sidebar, displays `วีดีโอที่สร้าง`, adds a short completed-video caption, and renders the extracted controller.

- [x] **Step 4: Run focused tests**

Run: `pytest test/services/test_cloud_agent_ui.py -k 'sidebar or video_library' -v && pytest test/services/test_completed_videos_page.py test/services/test_cloud_agent_webui.py -k 'completed_video or video_library' -v`

Expected: PASS.

### Task 3: Regression verification and focused commit

**Files:**
- Verify all files changed by Tasks 1–2.

**Interfaces:**
- No new interface.

- [x] **Step 1: Run complete WebUI tests**

Run: `pytest test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py test/services/test_completed_videos_page.py -v`

Expected: PASS.

- [x] **Step 2: Run backend and workflow regressions**

Run: `pytest test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_controller.py test/services/cloud_agent/test_worker.py test/services/cloud_agent/test_workflow.py -v`

Expected: PASS.

- [x] **Step 3: Run Ruff and inspect the diff**

Run: `ruff check webui/cloud_agent.py webui/cloud_agent_ui.py webui/completed_videos.py webui/pages/4_Completed_Videos.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py test/services/test_completed_videos_page.py && git diff --check && git status --short`

Expected: zero Ruff diagnostics, no whitespace errors, and only scoped files plus pre-existing untracked backups.

- [x] **Step 4: Commit the completed feature**

Run: `git add -f docs/superpowers/specs/2026-08-28-completed-videos-page-design.md docs/superpowers/plans/2026-08-28-completed-videos-page.md && git add webui/cloud_agent.py webui/cloud_agent_ui.py webui/completed_videos.py webui/pages/4_Completed_Videos.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py test/services/test_completed_videos_page.py && git commit -m "feat: move completed videos to dedicated page"`

Expected: one focused commit on `feature/cloud-video-agent`.
