# Task 15: Legacy Video Generation Retirement

## Decision

Retire the classic local video-generation product.  The retained production
surfaces are the Cloud Agent and Music Batch.  This is a deliberate product
retirement, not an attempt to infer that the legacy paths are unused.

## Scope

Remove the classic generation path as a coherent set:

- the legacy `/videos` generation entry point and its task-oriented UI;
- legacy stock/material-provider selection, local media URL/upload controls,
  material-type and mixed-material options;
- classic fixed six-clip local rendering and its MoviePy-dependent code;
- legacy task-pipeline branches that fetch stock media and concatenate/render
  a final video locally.

Retain without behavior changes:

- Cloud Agent API, worker, persistence, adaptive timing, Flow and Canva
  assembly;
- the Cloud Agent panel in `webui/Main.py` and its FastAPI-only architecture;
- Music Batch, including its GPU path and its currently retained callers;
- subtitle/audio-only services where they have a retained caller.

## Compatibility and safety

Classic generation requests will be rejected explicitly rather than silently
falling back to local rendering.  Historic legacy tasks may remain readable and
their existing output files remain downloadable; no stored media is deleted by
this code change.  Cloud Agent routes and persisted CloudJob records are not
migrated or altered.

No change may weaken these validated contracts:

- one canonical TTS synthesis per CloudJob;
- Google Flow pre-clean, concurrency fencing and durable recovery;
- Canva upload, timeline assembly, captioning, export and final validation;
- Music Batch processing and recovery;
- browser-profile isolation and session controls.

## Removal sequence

Each numbered category is its own RED → GREEN → regression → commit cycle.

1. Add retained-surface guards for Cloud Agent, Music Batch, historic task
   read/download behavior, and explicit rejection of legacy creation.
2. Remove legacy WebUI generation controls, material controls and six-clip
   local-editor controls, leaving the Cloud Agent and Music Batch entry points.
3. Remove the legacy video-creation route and classic task submission branch;
   preserve read/download endpoints required for historic artifacts.
4. Remove stock/material selection and download services only after static
   references are zero.
5. Remove the local six-clip renderer and its callers only after its dedicated
   tests and imports are gone.
6. Remove dependencies only when repository-wide references are zero; refresh
   `uv.lock`, run `uv lock --check`, then `uv sync --frozen`.

## Verification gates

For each removal cycle run focused tests for the changed boundary, the Cloud
Agent regression suite, Music Batch regression, and Ruff.  Before merge, run a
non-paid Cloud Agent session/health smoke and verify API, WebUI and Worker
remain independent.  A real paid generation is not required solely for legacy
retirement and must never be triggered by its tests.

## Inventory evidence

The initial inventory found live callers for all candidate legacy categories:

- `app/services/task.py` imports stock material selection and the local
  six-clip renderer;
- `webui/Main.py` renders the classic material and six-clip controls;
- `app/router.py` still includes `app.controllers.v1.video`;
- `moviepy` remains a declared dependency of the local render path;
- Music Batch has explicit page, service and test callers and is excluded.

Therefore this retirement is intentionally staged and cannot safely start by
deleting a purportedly-unused file.

## Approved shared-core refinement

Music Batch directly invokes `task.generate_script`, `task.generate_terms`,
`task.generate_audio`, `task.get_video_materials`, `task.generate_final_videos`
and `video.py` codec helpers. It therefore owns the shared stock/local-render
core and its MoviePy dependency. Task 15 retires the classic public Video
product, not that retained Music Batch implementation. The removable local
category is the classic six-clip editor/media/renderer, which has no Music
Batch caller.
