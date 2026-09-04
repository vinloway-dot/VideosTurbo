# Stock Image and Mixed Material Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Main Video Generator and Music Batch choose Video, Image, or Video + Image for supported stock sources, while reusing the existing local-image/video composition core and adding configurable Ken Burns motion for images.

**Architecture:** Keep existing video search/render behavior unchanged for the default `video` mode. Add a small stock-image service for Pexels/Pixabay, a material-mode orchestrator that can combine downloaded videos with image-derived clips, and a focused image-motion helper that produces normal MP4 clips before the existing final-video pipeline. Extend the existing request models, Main WebUI, and Music Batch global/per-song settings without changing Shengsuan or unsupported providers.

**Tech Stack:** Python 3.11/3.13, Pydantic v2, Streamlit, requests, MoviePy 2.x, FFmpeg, pytest.

**Spec:** User-approved conversation requirements for Material Type and Ken Burns support.

## Global Constraints

- Existing default behavior remains `Video` and must be backward compatible.
- Material types are exactly `video`, `image`, `mixed`.
- Image duration is integer 1–30 seconds, default 8.
- Image motion values are `slow_zoom_in`, `slow_zoom_out`, `pan_left_right`, `pan_right_left`, `random`, `none`.
- Pexels and Pixabay support Video, Image, and Mixed.
- Coverr and Shengsuan remain Video-only in this feature.
- Local keeps using the existing material pipeline; image behavior is extended, not replaced wholesale.
- Mixed mode alternates Video/Image when both are available and degrades to the available type instead of failing.
- Existing Video Generator and Music Batch GPU/NVENC behavior must not regress.

---

### Task 1: Domain models and compatibility validation

**Files:**
- Modify: `app/models/schema.py`
- Modify: `app/services/music_batch/models.py`
- Test: `test/services/test_material_type_models.py`
- Test: `test/services/music_batch/test_models.py`

**Interfaces:**
- Produces `MaterialType`, `ImageMotion`, `VideoParams.material_type`, `VideoParams.image_duration`, `VideoParams.image_motion`.
- Produces matching Music Batch global and per-song override fields.

- [ ] Write failing model/default/validation tests.
- [ ] Run targeted tests and verify RED because new fields/enums do not exist.
- [ ] Add minimal enums/fields and Music Batch resolution support.
- [ ] Run targeted tests and verify GREEN.

### Task 2: Stock image search/download for Pexels and Pixabay

**Files:**
- Create: `app/services/stock_images.py`
- Test: `test/services/test_stock_images.py`

**Interfaces:**
- Produces `search_images_pexels(...)`, `search_images_pixabay(...)`, `download_images(...)` returning downloaded local image paths plus provider metadata.
- Reuses existing API keys, proxy/TLS policy, aspect matching, safe source metadata, and task artifact recording from `app.services.material` where practical.

- [ ] Write failing API-response parsing tests for Pexels and Pixabay.
- [ ] Verify RED.
- [ ] Implement provider-specific photo/image parsing, aspect filtering, deterministic download names, safe TLS/proxy handling, and source records.
- [ ] Verify GREEN.

### Task 3: Ken Burns image-to-clip extension

**Files:**
- Create: `app/services/image_materials.py`
- Test: `test/services/test_image_materials.py`

**Interfaces:**
- Produces `prepare_image_clips(paths, output_dir, duration, motion, aspect)`.
- Output is ordinary MP4 clip paths compatible with the existing final render pipeline.

- [ ] Write failing tests for motion normalization/random selection and duration validation.
- [ ] Verify RED.
- [ ] Implement Slow Zoom In, Slow Zoom Out, Pan Left→Right, Pan Right→Left, Random-per-image, and None using MoviePy/FFmpeg-compatible clips.
- [ ] Preserve aspect/crop behavior and avoid black borders.
- [ ] Verify GREEN.

### Task 4: Material-mode orchestration

**Files:**
- Create: `app/services/stock_materials.py`
- Modify: `app/services/task.py`
- Test: `test/services/test_stock_materials.py`
- Test: `test/services/test_task.py`

**Interfaces:**
- Produces a single orchestration path for `video`, `image`, and `mixed`.
- `video` delegates to existing `material.download_videos` unchanged.
- `image` downloads images, converts them to MP4 image clips, and returns those paths.
- `mixed` downloads both sides, alternates when both exist, and falls back to whichever side is available.

- [ ] Write failing orchestration tests.
- [ ] Verify RED.
- [ ] Route `get_video_materials()` by material type without changing Shengsuan semantics.
- [ ] Ensure total requested duration is respected and final path inputs remain MP4-compatible.
- [ ] Verify GREEN.

### Task 5: Main WebUI controls

**Files:**
- Modify: `webui/Main.py`
- Test: `test/test_main.py` and/or `test/services/test_webui_generation_defaults.py`

**Interfaces:**
- Add `Material Type` selectbox beneath `Video Source`.
- Show `Image Duration` and `Ken Burns Effect` only for Image/Mixed.
- Restrict unsupported source/type combinations and preserve Video default.

- [ ] Write failing UI-source/default tests.
- [ ] Verify RED.
- [ ] Add controls and persist values through existing config/session helpers.
- [ ] Pass fields into `VideoParams`.
- [ ] Verify GREEN.

### Task 6: Music Batch global and per-song overrides

**Files:**
- Modify: `webui/music_batch.py`
- Modify: `app/services/music_batch/manager.py`
- Test: `test/test_music_batch_webui.py`
- Test: `test/services/music_batch/test_manager.py`

**Interfaces:**
- Global: material type, image duration, image motion.
- Per-song override: same fields plus Reset to Global behavior.
- Manager forwards resolved values into `VideoParams` for each provider plan.

- [ ] Write failing global/override/manager tests.
- [ ] Verify RED.
- [ ] Implement controls, reset keys, model propagation, and report/state persistence.
- [ ] Verify GREEN.

### Task 7: Regression and integration gates

**Files:**
- Test: existing suite plus new tests.

- [ ] Run Python 3.11 test suite and Ruff.
- [ ] Run Python 3.13 suite in CI.
- [ ] Run Windows smoke tests in CI.
- [ ] Verify default Video mode remains unchanged.
- [ ] Verify Pexels/Pixabay Image and Mixed logic with mocked API fixtures.
- [ ] Open a draft PR only after RED/GREEN commits are visible; do not merge until CI and a local Windows visual smoke test pass.
