# Cloud Agent Video Library — final fix report

## Status

All final-review Critical and Important findings were fixed. Both adjacent minors were also fixed: Cancel reruns immediately, and purge is an idempotent no-op when the staged target or storage root is missing. No Flow, Canva, provider, worker, session, credit, or external-data behavior was changed.

## Changes

### C1 — Streamlit-safe final-video playback

- Added a narrowly validated `_load_video_media()` boundary that accepts only `/api/v1/cloud-agent/jobs/{job_id}/final`, fetches that internal API response on the Streamlit server, and returns bytes.
- `render_video_library()` now passes those bytes to `st.video(..., format="video/mp4")`. Streamlit's media manager therefore creates a browser-served media URL instead of interpreting the API-relative URL as a local filename.
- Public library payloads and view models remain path-free; no local artifact path reaches the UI.
- Added a production-faithful Streamlit 1.59.1 characterization using the real `MediaFileManager`/`MemoryMediaFileStorage`, plus a real marshalling assertion proving fetched bytes become `/media/...`.
- Self-review added a boundary regression proving non-API relative paths are rejected before any request.

### C2 — `.deleting` TOCTOU safety

- Replaced path validation followed by path mutation with descriptor-relative operations.
- The storage root and `.deleting` are opened with `O_DIRECTORY | O_NOFOLLOW`; `.deleting` is rechecked by device/inode against the opened descriptor.
- Stage and restore use `os.rename(..., src_dir_fd=..., dst_dir_fd=...)`; purge uses symlink-resistant `shutil.rmtree(..., dir_fd=...)`.
- Stage rolls the job directory back and raises if the `.deleting` entry changes during the rename.
- Deterministic tests swap `.deleting` to an external symlink exactly at the stage rename, restore rename, and purge call. Each test proves the external sentinel/tree is unchanged.
- Purge now returns without creating storage when either the storage root or staged target is absent.

### I1/I2 and Cancel minor — deletion UI outcomes

- DELETE and refresh now have separate exception boundaries.
- A successful DELETE clears the confirmation state immediately. A later GET failure reports that deletion succeeded but refresh failed, returns success to the callback, and reruns; retry cannot issue a false second DELETE/404.
- Typed API failures show Thai deletion-specific framing plus the API detail and a refresh/retry action.
- Connection failures show Thai instructions to check the connection and retry.
- Cancel clears only the pending deletion ID and reruns immediately without calling DELETE.

### I3 — responsive newest-first ordering

- The renderer now emits sequential five-card rows rather than distributing cards modulo five into long columns.
- Existing responsive CSS reflows each source-ordered row at 5/3/2/1 columns without changing visual order.
- Added a rendered Playwright test that derives DOM structure from the real renderer calls and verifies the literal visual order `job-01` through `job-10` at 1200, 1000, 700, and 400 px.

## RED/GREEN evidence

### Storage race and missing-root RED

Command:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py -k 'swapped or missing_staged_target' -v
```

RED result: `4 failed, 11 deselected`.

- Stage did not raise and moved the job into the external symlink target.
- Restore moved the external victim into the storage root; the genuine final file remained displaced.
- Purge deleted the external victim.
- Missing storage root raised `FileNotFoundError`.

GREEN command:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py -k 'swapped or missing_staged_target or symlinked_deleting_root or staged_job_can' -v
```

GREEN result: `6 passed, 9 deselected`.

Full storage/library GREEN: `15 passed` from `uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py -v`.

### Streamlit media RED/GREEN

Initial command:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_ui.py::test_streamlit_159_treats_api_relative_video_url_as_a_local_filename test/services/test_cloud_agent_ui.py::test_video_library_server_fetches_bytes_into_streamlit_media_manager test/services/test_cloud_agent_webui.py::test_video_library_media_is_fetched_from_internal_api_as_bytes -v
```

RED result: `3 failed`.

- The real Streamlit manager wrapped the underlying missing local file in `MediaFileStorageError`; the characterization expectation was corrected without changing production code.
- The renderer rejected the missing `load_video` contract with `TypeError`.
- `_load_video_media` was absent (`AttributeError`).

GREEN result for the same command after the production fix and corrected characterization: `3 passed`.

Self-review boundary RED:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_webui.py::test_video_library_media_rejects_non_api_paths_before_request -v
```

Result: `1 failed`; `job/final` reached the request function. After the prefix fix, the focused valid/invalid pair passed: `2 passed`.

### Deletion state/error RED/GREEN

Command:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_webui.py -k 'video_delete_cancel or successful_delete_is_not or typed_video_delete or video_delete_connection' -v
```

RED result: `4 failed, 54 deselected` (no cancel rerun, successful DELETE returned false after refresh failure, raw typed English, generic English connection error).

GREEN result for the same command: `4 passed, 54 deselected`.

### Responsive ordering RED/GREEN

Command:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_ui.py::test_video_library_visual_order_stays_newest_first_at_responsive_widths -v
```

RED result: `1 failed`; at the 3-column breakpoint the visual order was `1,2,3,6,7,8,4,5,9,10`.

GREEN result for the same command: `1 passed`.

## Final verification

- Focused changed-area suites:
  - `uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_ui.py test/services/test_cloud_agent_webui.py -q`
  - Result: `104 passed in 8.24s`.
- Full repository suite:
  - `uv run python -X utf8 -m pytest`
  - Final pre-commit result: `1317 passed, 23 skipped, 12 warnings in 47.96s`.
  - Warnings are existing Starlette/Pydantic/pydub deprecation warnings; no new failure or warning category was introduced.
- Repository-wide lint:
  - `uv run ruff check .`
  - Result: `All checks passed!`
- Whitespace validation:
  - `git diff --check`
  - Result: clean (no output).

## Self-review

- C1 mutation check: replacing the byte callback with `card.final_url` fails the real Streamlit marshalling regression; accepting `job/final` fails the boundary test.
- C2 mutation check: reverting any of stage/restore/purge to path-based operations mutates or deletes the external sentinel in its corresponding deterministic test.
- I1 mutation check: moving GET back into the DELETE exception boundary makes successful deletion return false and leaves the pending ID set.
- I2 mutation check: routing deletion errors through `_api_error_message` fails both Thai actionable-copy tests.
- I3 mutation check: restoring modulo-five placement fails the Playwright test at 3/2/1 widths.
- Cancel mutation check: removing `st.rerun()` fails the immediate-rerun assertion.
- Storage scope remains confined to `CloudJobStorage`; API/service interfaces are unchanged.
- Media fetch accepts only the existing final endpoint. No filesystem path, external URL, Flow/Canva action, provider request, browser session, worker state, or credit path was added.

## Concerns

- Descriptor-relative `O_DIRECTORY`/`O_NOFOLLOW` safety is POSIX-specific, matching the deployed/tested Linux environment. A future non-POSIX port must preserve the same indivisible no-follow guarantee rather than fall back to path validation.
- Server-fetching up to ten final videos intentionally trades backend bandwidth/memory for correct Streamlit browser delivery; this is the safe option when the API's loopback address is not browser-reachable.
- No physical browser smoke against a deployed API with real final MP4 files was performed; the installed Streamlit 1.59.1 marshaller and Playwright-rendered responsive layout are covered directly in automated tests.
- The requested independent reviewer subagent was not used because this fix wave explicitly prohibited spawning agents; the complete diff was self-reviewed instead.
