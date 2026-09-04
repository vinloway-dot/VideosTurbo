# Completed Videos Page Design

## Goal

Move the completed-video library out of the Cloud Agent production page and expose it through a dedicated sidebar page named `วีดีโอที่สร้าง`.

## Scope

- Add a real Streamlit sidebar link directly below Cloud Agent.
- Add a dedicated wide-layout page with the title `วีดีโอที่สร้าง`.
- Preserve the existing 5 × 2 grid, newest-first API ordering, numbered pagination, per-card media isolation, and confirmed deletion behavior.
- Remove video-library fetching and rendering from `render_cloud_agent_panel()`.
- Load the video library only when its dedicated page is rendered.

## Architecture

Move the completed-video WebUI controller functions from `webui/cloud_agent.py` into `webui/completed_videos.py`. The new page owns only presentation and session state; it reuses the existing `cloud_agent_ui.video_library_view()` and `cloud_agent_ui.render_video_library()` functions and the existing Cloud Agent video API.

This change does not alter the Worker, workflow, database, job status transitions, Google Flow, Canva, export, provider calls, storage layout, or API contracts. A video-list or media failure stays inside the completed-videos page and cannot prevent the Cloud Agent production controls from rendering.

## Error and deletion behavior

- A list request failure shows a page-local error and returns.
- A single media failure shows the existing card placeholder while other cards remain available.
- Deletion keeps the existing confirmation and local VideosTurbo storage scope.
- Page correction after deleting the last item keeps the user on the nearest valid numbered page.

## Verification

- Controller tests verify the fixed page size, media-path validation, pagination, confirmation, and page-local failure behavior.
- Navigation tests verify the sidebar link and page entry point.
- Cloud Agent integration tests verify that its panel no longer fetches or renders the video library.
- Existing UI, API, worker, and workflow regression tests remain green.
