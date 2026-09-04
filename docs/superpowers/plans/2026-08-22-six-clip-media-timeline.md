# Six-Clip Media Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fixed six-clip, 60-second Main Generator timeline driven only by user-provided/uploaded media, with AI-generated per-clip prompts and a live master prompt.

**Architecture:** Keep the existing stock material services for Music Batch and backwards compatibility, but route Main Generator tasks with `six_clip_mode=true` through isolated six-clip planning, media, and rendering modules. Section 1 remains the TTS/subtitle script source; Section 2 owns visual media and prompts; Section 3 is derived from Section 2.

**Tech Stack:** Python 3.11/3.13, Pydantic, Streamlit, requests, MoviePy/FFmpeg, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-22-six-clip-media-timeline-design.md`

## Global Constraints

- Develop only on `feature/six-clip-media-timeline`.
- Do not merge into `feature/material-type-mixed` or `main`.
- Exactly six clips: 0–10, 10–20, 20–30, 30–40, 40–50, 50–60 seconds.
- Section 1 is the only TTS/subtitle source.
- Main Generator must not call stock-material providers in six-clip mode.
- Missing media in any clip blocks rendering before TTS/paid services.
- Signed URL query strings are never persisted after successful import.
- Final visual timeline is exactly 60 seconds; narration >60 seconds fails instead of being truncated.

---

### Task 1: Six-clip data model and pure plan helpers

**Files:**
- Create: `app/models/six_clip.py`
- Create: `app/services/six_clip_plan.py`
- Modify: `app/models/schema.py`
- Test: `test/services/test_six_clip_plan.py`

**Interfaces:**
- Produces: `SixClipSegment`, `SixClipPlan`, `build_master_prompt(plan)`, `validate_six_clip_plan(plan)`, `parse_ai_clip_plan(text)`.

- [ ] Write failing tests for exact segment count/order/timestamps, editable values, and master prompt structure.
- [ ] Run focused tests and confirm RED.
- [ ] Implement models, validation, fixed global-character preamble, JSON AI response parser, and master prompt builder.
- [ ] Add backwards-compatible `six_clip_mode`/`six_clip_plan` fields to `VideoParams`.
- [ ] Run focused tests and Ruff; confirm GREEN.
- [ ] Commit.

### Task 2: AI generation contract for Target Words + six clip analysis

**Files:**
- Modify: `app/services/llm.py`
- Modify: `webui/Main.py`
- Test: `test/services/test_six_clip_ai.py`

**Interfaces:**
- Produces: `llm.generate_six_clip_plan(video_script, language, target_words, app_config=None)`.

- [ ] Write failing tests asserting script prompt includes target word count and 0–3 second hook requirement without breaking legacy calls.
- [ ] Write failing tests for six-clip analyzer prompt requiring six ordered 10-second clips and detailed English prompts with 0–3/3–6/6–10 beats.
- [ ] Implement optional target-word support in script generation while preserving existing default behavior.
- [ ] Implement six-clip analyzer using strict JSON output and `parse_ai_clip_plan`.
- [ ] Run focused tests + existing LLM tests + Ruff.
- [ ] Commit.

### Task 3: Safe direct-media import and upload persistence

**Files:**
- Create: `app/services/six_clip_media.py`
- Test: `test/services/test_six_clip_media.py`

**Interfaces:**
- Produces: `import_media_url(url, destination_dir, clip_index)`, `save_uploaded_media(...)`, `validate_ready_media(plan)`, `redact_media_url(url)`.

- [ ] Write RED tests for Google-Flow-style no-extension URL accepting `video/mp4`.
- [ ] Write RED tests for JPEG/PNG/WebP, HTML rejection, invalid schemes, max-size handling, expired/failing URLs, and query redaction.
- [ ] Implement streamed HTTP import with timeouts, bounded bytes, MIME/header validation, extension inference, and sanitized local filenames.
- [ ] Ensure returned plan stores only local path/media kind, not original signed URL.
- [ ] Run focused tests + Ruff.
- [ ] Commit.

### Task 4: Deterministic 10-second segment renderer

**Files:**
- Create: `app/services/six_clip_render.py`
- Modify only if required: `app/services/video.py`, `app/services/image_materials.py`
- Test: `test/services/test_six_clip_render.py`

**Interfaces:**
- Produces: `prepare_six_clip_timeline(task_id, plan, video_aspect, image_motion, threads, video_codec) -> list[str]`, `concat_six_clip_timeline(...) -> str`.

- [ ] Write RED tests for fixed order, >10s video trim, <10s video loop, source-audio removal, image exact 10s, and six prepared clips.
- [ ] Implement preparation using MoviePy/FFmpeg and existing codec-fallback/image-motion helpers where practical.
- [ ] Concatenate exactly six prepared clips in order to an exact 60-second combined video without using narration duration as the trim limit.
- [ ] Run focused tests + video/image regressions + Ruff.
- [ ] Commit.

### Task 5: Task pipeline routing and fail-closed preflight

**Files:**
- Modify: `app/services/task.py`
- Test: `test/services/test_six_clip_task_pipeline.py`

**Interfaces:**
- Consumes: `VideoParams.six_clip_mode`, `VideoParams.six_clip_plan`, `prepare_six_clip_timeline`.

- [ ] Write RED test proving missing Clip N fails before `generate_script`, TTS, or material downloader.
- [ ] Write RED test proving stock downloader is never called in six-clip mode.
- [ ] Write RED test proving narration >60s fails with actionable error.
- [ ] Route six-clip mode around term generation and stock material acquisition.
- [ ] Keep Section 1 TTS/subtitle path unchanged.
- [ ] Use fixed 60-second combined timeline and existing final audio/subtitle/BGM muxing.
- [ ] Preserve legacy pipeline when `six_clip_mode=false`.
- [ ] Run focused + task regressions + Ruff.
- [ ] Commit.

### Task 6: Section 2 / Section 3 Streamlit UI

**Files:**
- Create: `webui/six_clip_timeline.py`
- Modify: `webui/Main.py`
- Test: `test/services/test_six_clip_webui.py`

**Interfaces:**
- Produces: `render_six_clip_timeline(plan, ...) -> SixClipPlan` and stable session-state keys per clip.

- [ ] Write source-level/logic tests for Target Words, six visible clip ranges, editable narration/prompt fields, URL/Upload source selector, Import Media action, preview/readiness state, and live Section 3.
- [ ] Add Target Words near Section 1 script settings.
- [ ] On Generate Script & Keywords, also populate Section 2 through the analyzer.
- [ ] Render six clip cards and Section 3 via the new UI module.
- [ ] Remove Main Generator use/display of legacy stock Video Source/Material Type controls in this branch while leaving services intact.
- [ ] Save URL imports immediately; save upload bytes task-locally before submit.
- [ ] Pass `six_clip_mode=true` and current six-clip plan into task submission.
- [ ] Run focused WebUI tests + compile + Ruff.
- [ ] Commit.

### Task 7: Task history restore and safety persistence

**Files:**
- Modify: `webui/Main.py`
- Modify if required: `app/services/task_artifacts.py`
- Test: `test/services/test_six_clip_history.py`

**Interfaces:**
- Persists: target words, Section 1 script/keywords, six narration contexts/prompts, local media references only.

- [ ] Write RED tests that history contains no signed URL query string and restores six clip prompts/media paths.
- [ ] Implement serialization/restore through existing task artifact patterns.
- [ ] Ensure missing/deleted local media restores as Missing rather than silently falling back to stock.
- [ ] Run focused tests + history regressions + Ruff.
- [ ] Commit.

### Task 8: Full verification and draft PR

**Files:**
- No production changes unless verification finds a defect.

- [ ] Run `uv run pytest -q test` on Python 3.11-equivalent environment.
- [ ] Run Ruff and compile checks.
- [ ] Ensure Windows smoke CI passes.
- [ ] Open/maintain a Draft PR from `feature/six-clip-media-timeline` to `feature/material-type-mixed` for review/CI only.
- [ ] Confirm final changed-file list contains no temporary patch workflows/scripts.
- [ ] Provide the user download/clone instructions for a new Windows folder; do not merge the branch.
