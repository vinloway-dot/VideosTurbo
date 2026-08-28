# Cloud Agent Video Library Design

## Goal

Show completed Cloud Agent final videos directly below **Production status**. The
library lets an operator browse recent results and permanently remove a video
from VideosTurbo cloud storage when it is no longer wanted. It does not change
the Script → Voice → Flow → Canva → Export workflow and it never deletes data
from Google Flow or Canva.

## Accepted Product Decisions

- The library title is **วีดีโอที่สร้าง**.
- A page displays 10 videos in a 5 × 2 desktop grid.
- Videos are ordered by `completed_at` descending, with job ID descending as a
  stable tie-breaker. New completed videos therefore appear first.
- Numbered pagination is shown when more than 10 completed videos exist.
- A job enters the library automatically when it is `COMPLETED`, has the final
  checkpoint, and its validated final video file exists.
- Each card has a **ลบ** button. The user must confirm the irreversible action.
- Deletion removes the job record and all of that job's local Cloud Agent
  artifacts from VideosTurbo cloud storage. It does not contact or delete
  anything in Google Flow or Canva.

## Placement and UI

```text
Production status
    ↓
วีดีโอที่สร้าง (5 × 2 video card grid, pagination)
    ↓
Job controls
```

Each video card contains an inline playable video, the job subject, its
completion time, and the delete control. The grid responds below desktop width
without changing the desktop 5 × 2 contract. On a successful deletion, the UI
reloads the current library page. If it becomes empty and a previous page
exists, the UI returns to that previous page.

The already-running production-status refresh detects terminal completion and
refreshes the application view, so a newly completed job appears in the
library without a manual refresh.

## API and Data Boundaries

Add a dedicated library API rather than loading all jobs into the WebUI:

### `GET /cloud-agent/videos?page=1&page_size=10`

Returns only visible library entries plus pagination metadata:

```json
{
  "items": [
    {
      "job_id": "uuid",
      "subject": "Video subject",
      "completed_at": "2026-08-28T00:00:00+00:00",
      "final_url": "/cloud-agent/jobs/uuid/final"
    }
  ],
  "page": 1,
  "page_size": 10,
  "total_items": 24,
  "total_pages": 3
}
```

`page` must be at least 1. The endpoint owns filtering, existence validation,
ordering, and paging; it does not expose local artifact paths.

### `DELETE /cloud-agent/videos/{job_id}`

This endpoint accepts only a library-visible completed job. It revalidates the
job ID and local storage boundary, then removes the job's dedicated artifact
directory and its Cloud Agent job record. The API returns a successful empty
response only once neither is visible to the library. A request for an
unknown, non-completed, non-final, or already-deleted job returns a typed
client error and makes no change.

To avoid a half-deleted visible library item, storage removal is staged inside
the Cloud Agent storage root. If validation or removal fails before the record
is deleted, the original job directory remains intact and the job stays in the
library. The record is deleted only after final-artifact removal succeeds.

No existing job-control endpoint or provider adapter changes. The current
final-video endpoint remains the sole media source for playback.

## Deletion Safety and Failure Handling

- Artifact paths are derived from the trusted job ID through `CloudJobStorage`;
  deletion must reject a path outside the configured Cloud Agent storage root.
- Only terminal `COMPLETED` jobs that satisfy the final-video validation are
  eligible. Active, queued, failed, cancelled, or human-required jobs cannot
  be deleted through this API.
- The UI requires a second confirmation and describes the deletion as
  permanent within VideosTurbo cloud.
- A failed deletion leaves the card visible and shows a Thai actionable error.
  It must not silently remove the card or report success.
- The request never uses a browser session, paid provider call, Flow action,
  or Canva action.

## Components

- `CloudJobStore`: provides an ordered, paginated query for library-eligible
  records and a narrowly scoped record deletion operation.
- `CloudJobStorage`: provides safe, job-scoped artifact existence and removal
  methods; it owns filesystem boundary validation.
- Cloud Agent API controller: maps the two library routes, validates inputs,
  and converts expected problems to typed HTTP errors.
- `webui/cloud_agent.py`: fetches and renders the paginated library between
  Production status and Job controls, keeps current page in session state, and
  refreshes after status completion/deletion.
- `webui/cloud_agent_ui.py` and the Cloud Agent stylesheet: contain reusable
  card, grid, pagination, and confirmation rendering so the main page remains
  orchestration-focused.

## Verification

Tests will cover:

1. Library eligibility excludes every non-completed or missing-final-video job.
2. Ordering is newest completion first and pagination metadata is correct.
3. The final URL is generated without exposing filesystem paths.
4. Deletion succeeds only for an eligible completed job and removes both record
   and job-scoped artifacts.
5. Invalid IDs, active jobs, missing files, and path-escape attempts leave all
   data unchanged and return the expected typed error.
6. WebUI rendering creates the 5 × 2 page size, numbered pages, confirmation
   behavior, successful refresh, and empty-page fallback.
7. Existing Cloud Agent, progress UI, and final-video route tests remain
   passing.

## Non-goals

- No deletion of Google Flow projects, generated clips, or Canva designs.
- No workflow, worker scheduling, provider, session, or credit-usage change.
- No new standalone database table or schema migration in version one.
