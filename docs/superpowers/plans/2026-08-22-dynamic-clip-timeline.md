# Dynamic Clip Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed six-clip/60-second Main Generator limit with an explicitly confirmed narration-driven timeline that keeps six slots up to 60 seconds and adds one 10-second visual slot for every additional narration interval.

**Architecture:** Preserve the persisted `SixClipPlan`, `SixClipSegment`, `six_clip_mode`, and `six_clip_plan` names, but generalize their ranges and metadata. Reuse the existing full voice-preview audio, subtitle timing object, provider fingerprint, FFmpeg normalization, task state, subtitle, BGM, and final muxing paths; the new confirmation flow makes exact measured narration duration authoritative and blocks stale plans before final submission.

**Tech Stack:** Python 3.11/3.13, Pydantic 2, Streamlit 1.59, existing TTS providers, existing LLM service, MoviePy/FFmpeg, pytest 9, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-22-dynamic-clip-timeline-design.md`

## Global Constraints

- Develop only on `feature/six-clip-media-timeline` and continue through Draft PR #3.
- Do not merge into `feature/material-type-mixed` or `main` without explicit user approval.
- `Generate Script` performs text generation only and never calls TTS.
- `Confirm Script & Build Timeline` is the only WebUI action that may create authoritative narration audio and a new plan.
- Authoritative duration is a finite positive float measured from generated or uploaded audio, never the word-count estimate and never a rounded integer.
- Use `clip_count = max(6, ceil(D / 10.0))` and `timeline_duration_sec = max(60.0, D)`.
- `app.max_dynamic_clip_count = 0` means no configured product cap; a positive value is an operational safety limit.
- No automatic voice-rate change, no repeated first-six fallback, and no stock-material fallback in timeline mode.
- Preserve existing public `six_clip_*` names and load legacy six-segment JSON with 60-second defaults.
- Final submission requires a current narration fingerprint, a reusable narration payload in WebUI, and valid media for every required range.
- Keep signed URL query values out of logs, plans, task history, and user-facing errors.
- Run focused tests after every task; Python 3.11, Python 3.13, Ruff, compile, Windows smoke, and the complete test suite are release gates.

---

## File responsibility map

| File | Responsibility after this change |
|---|---|
| `app/models/six_clip.py` | Backward-compatible dynamic segment/plan schema and legacy defaults |
| `app/models/schema.py` | Persist unchanged public timeline fields on `VideoParams` |
| `app/services/six_clip_plan.py` | Range calculation, plan validation/currentness, cue partitioning, dynamic LLM contracts, and prompt batches |
| `app/services/voice.py` | Normalize Edge `cues` and legacy `subs/offset` into timed narration cues |
| `app/services/six_clip_media.py` | Safe URL/upload import and task-local materialization for any positive plan index |
| `app/services/six_clip_render.py` | Ordered dynamic preparation, final partial trim, concat duration cap, and duration verification |
| `app/services/task.py` | Backend preflight, exact audio measurement, plan/audio agreement, and no-stock dynamic routing |
| `webui/Main.py` | Text-only script generation, explicit narration confirmation, stale detection, and safe final submission |
| `webui/six_clip_timeline.py` | Six-card pagination, media preservation by exact range, dynamic cards, and master-prompt batches |
| `config.example.toml` | Document optional `max_dynamic_clip_count` safety limit |
| `.github/workflows/ci.yml` | Include dynamic timeline tests in Windows smoke |
| `test/services/test_six_clip_*.py` | Dynamic model, AI, media, render, pipeline, UI, and history regressions |
| `test/services/test_webui_voice_preview.py` | Explicit confirmation, cache reuse, fingerprint invalidation, and no-double-volume regression |
| `test/services/test_voice.py` | Timed-cue normalization for supported `SubMaker` shapes |

---

### Task 1: Generalize the persisted plan and pure range contract

**Files:**
- Modify: `app/models/six_clip.py:1-70`
- Verify unchanged public fields: `app/models/schema.py:95-101`
- Modify: `app/services/six_clip_plan.py:1-78`
- Modify: `config.example.toml` under `[app]`
- Modify: `test/services/test_six_clip_plan.py:1-101`
- Modify: `test/services/test_config.py:32-55`

**Interfaces:**
- Produces: `build_timeline_ranges(narration_duration_sec: float, *, slot_duration_sec: float = 10.0, minimum_clip_count: int = 6, maximum_clip_count: int = 0) -> tuple[tuple[float, float], ...]`.
- Produces: `validate_timeline_plan(plan: SixClipPlan) -> SixClipPlan`.
- Produces: `is_timeline_current(plan: SixClipPlan, narration_fingerprint: str) -> bool`.
- Preserves: `validate_six_clip_plan` as a compatibility delegate to `validate_timeline_plan`.

- [ ] **Step 1: Replace the fixed-six model tests with failing boundary and legacy tests**

```python
@pytest.mark.parametrize(
    ("duration", "count", "last_range"),
    [
        (55.0, 6, (50.0, 60.0)),
        (60.0, 6, (50.0, 60.0)),
        (60.1, 7, (60.0, 60.1)),
        (63.0, 7, (60.0, 63.0)),
        (88.0, 9, (80.0, 88.0)),
        (127.0, 13, (120.0, 127.0)),
    ],
)
def test_build_timeline_ranges_uses_exact_narration_duration(
    duration, count, last_range
):
    ranges = build_timeline_ranges(duration)
    assert len(ranges) == count
    assert ranges[-1] == last_range

def test_legacy_six_plan_json_loads_with_sixty_second_defaults():
    plan = SixClipPlan.model_validate({"target_words": 130, "segments": legacy_segments()})
    assert plan.narration_duration_sec == 60.0
    assert plan.timeline_duration_sec == 60.0
    assert plan.slot_duration_sec == 10.0
    assert plan.narration_fingerprint == ""

def test_configured_maximum_rejects_required_count():
    with pytest.raises(ValueError, match="requires 13 clips.*maximum is 12"):
        build_timeline_ranges(127.0, maximum_clip_count=12)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_plan.py test/services/test_config.py`

Expected: FAIL because the model is capped at six, the new metadata and helpers do not exist, and `config.example.toml` does not document the cap.

- [ ] **Step 3: Implement finite-duration validation, exact ranges, and compatible models**

```python
def build_timeline_ranges(
    narration_duration_sec: float,
    *,
    slot_duration_sec: float = 10.0,
    minimum_clip_count: int = 6,
    maximum_clip_count: int = 0,
) -> tuple[tuple[float, float], ...]:
    duration = float(narration_duration_sec)
    slot = float(slot_duration_sec)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("narration duration must be a finite positive number")
    if not math.isfinite(slot) or slot <= 0:
        raise ValueError("slot duration must be a finite positive number")
    count = max(int(minimum_clip_count), math.ceil(duration / slot))
    if maximum_clip_count > 0 and count > maximum_clip_count:
        raise ValueError(
            f"narration requires {count} clips; configured maximum is "
            f"{maximum_clip_count}"
        )
    timeline = max(slot * int(minimum_clip_count), duration)
    return tuple(
        (
            index * slot,
            min((index + 1) * slot, timeline),
        )
        for index in range(count)
    )
```

Define `SixClipSegment.index` with `ge=1` and no `le=6`, finite float start/end fields, and `end_sec > start_sec`. Define `SixClipPlan` with the four approved defaults and `segments: list[SixClipSegment] = Field(min_length=6)`. Validate ordered absolute indexes and exact calculated ranges with a small absolute tolerance of `1e-6`.

- [ ] **Step 4: Add compatibility and currentness helpers**

```python
def validate_six_clip_plan(plan: SixClipPlan) -> SixClipPlan:
    return validate_timeline_plan(plan)

def is_timeline_current(plan: SixClipPlan, narration_fingerprint: str) -> bool:
    expected = str(narration_fingerprint or "").strip()
    return bool(expected and plan.narration_fingerprint == expected)
```

Keep `empty_six_clip_plan()` producing the historical six ranges and default metadata. Add `max_dynamic_clip_count = 0` to `config.example.toml` with a comment that positive values limit confirmation before visual-plan generation.

- [ ] **Step 5: Run focused tests and Ruff; confirm GREEN**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_plan.py test/services/test_schema.py test/services/test_config.py`

Run: `uv run --no-sync ruff check app/models/six_clip.py app/services/six_clip_plan.py test/services/test_six_clip_plan.py test/services/test_config.py`

Expected: all commands PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/models/six_clip.py app/services/six_clip_plan.py config.example.toml test/services/test_six_clip_plan.py test/services/test_config.py
git commit -m "feat: generalize six clip timeline model"
```

---

### Task 2: Build dynamic narration chunks, AI ranges, and prompt batches

**Files:**
- Modify: `app/services/voice.py:542-628,1840-1967`
- Modify: `app/services/six_clip_plan.py:50-187`
- Modify: `test/services/test_voice.py`
- Modify: `test/services/test_six_clip_ai.py:1-74`
- Modify: `test/services/test_six_clip_plan.py`

**Interfaces:**
- Produces: `extract_timed_text_cues(sub_maker) -> tuple[tuple[float, float, str], ...]` in `voice.py`.
- Produces: `partition_narration_cues(cues, ranges) -> tuple[str, ...]`.
- Produces: `build_timeline_analysis_prompt(video_script, ranges, language="", narration_chunks=None) -> str`.
- Produces: `generate_six_clip_plan(..., timeline_ranges, narration_duration_sec, narration_fingerprint, subtitle_cues=None, app_config=None) -> SixClipPlan`.
- Produces: `build_master_prompt_batches(plan, batch_size=6) -> tuple[str, ...]`.
- Preserves: `build_master_prompt(plan) -> str` for existing callers by joining generated batches.

- [ ] **Step 1: Write failing cue, dynamic-output, and batching tests**

```python
def test_extract_timed_text_cues_supports_legacy_offsets():
    sub_maker = SimpleNamespace(
        subs=["First.", "Second."],
        offset=[(0, 100_000_000), (100_000_000, 200_000_000)],
    )
    assert extract_timed_text_cues(sub_maker) == (
        (0.0, 10.0, "First."),
        (10.0, 20.0, "Second."),
    )

def test_dynamic_ai_contract_requires_exact_supplied_ranges(monkeypatch):
    ranges = build_timeline_ranges(63.0)
    plan = generate_six_clip_plan(
        "Full narration",
        timeline_ranges=ranges,
        narration_duration_sec=63.0,
        narration_fingerprint="fingerprint",
        subtitle_cues=((0.0, 9.0, "Opening"), (60.0, 63.0, "Ending")),
    )
    assert len(plan.segments) == 7
    assert plan.segments[-1].end_sec == 63.0
    assert plan.narration_fingerprint == "fingerprint"

def test_master_prompt_batches_use_absolute_indexes():
    batches = build_master_prompt_batches(dynamic_plan(13), batch_size=6)
    assert len(batches) == 3
    assert "7 clips" not in batches[0]
    assert "CLIP 7" in batches[1]
    assert "CLIP 13" in batches[2]
```

Also test missing, extra, reordered, and overlapping AI objects; verify the partial 60–63 prompt tells the generator to finish the action within the first three seconds.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_voice.py test/services/test_six_clip_ai.py test/services/test_six_clip_plan.py`

Expected: FAIL because timed cue extraction, dynamic ranges, and prompt batches do not exist.

- [ ] **Step 3: Normalize supported subtitle timing shapes**

Add `extract_timed_text_cues` to `voice.py`. Read Edge `cue.start/end.total_seconds()` and `cue.content` first; fall back to legacy `offset/subs` where offsets are 100-nanosecond units. Drop empty text and invalid/non-positive ranges, preserve order, and HTML-unescape content.

- [ ] **Step 4: Implement exact range-driven AI validation**

The analysis prompt must serialize the supplied absolute indexes and ranges and demand one JSON object per range. Parse the response against the supplied ranges instead of assigning ranges with `zip(SIX_CLIP_RANGES, clips)`.

```python
actual = [
    (int(clip["index"]), float(clip["start_sec"]), float(clip["end_sec"]))
    for clip in clips
]
expected = [
    (index, start, end)
    for index, (start, end) in enumerate(timeline_ranges, start=1)
]
if actual != expected:
    raise ValueError("timeline AI response does not match the requested ranges")
```

When timed cues are usable, prefill `narration_context` by assigning each cue to the range containing its midpoint, then ask the LLM only for titles and visual prompts. Without usable cues, include the complete script and require chronological partitioning in the existing LLM request.

- [ ] **Step 5: Implement dynamic master-prompt batches**

Each batch contains at most six segments, repeats `GLOBAL_CHARACTER_RULES`, states the total clip count and absolute covered range, and uses the existing editable title/narration/prompt values. For a partial final range, state the exact usable seconds.

- [ ] **Step 6: Run focused tests and Ruff; confirm GREEN**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_voice.py test/services/test_six_clip_ai.py test/services/test_six_clip_plan.py test/services/test_llm.py`

Run: `uv run --no-sync ruff check app/services/voice.py app/services/six_clip_plan.py test/services/test_voice.py test/services/test_six_clip_ai.py test/services/test_six_clip_plan.py`

Expected: all commands PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add app/services/voice.py app/services/six_clip_plan.py test/services/test_voice.py test/services/test_six_clip_ai.py test/services/test_six_clip_plan.py
git commit -m "feat: generate range driven clip prompts"
```

---

### Task 3: Convert full voice preview into explicit narration confirmation

**Files:**
- Modify: `webui/Main.py:2750-2801,3658-4057,4503-4950,5539-5599`
- Modify: `webui/six_clip_timeline.py:1-143`
- Modify: `test/services/test_webui_voice_preview.py:66-490`
- Modify: `test/services/test_six_clip_webui.py:1-52`

**Interfaces:**
- Consumes: existing `_voice_preview_fingerprint`, `_get_voice_preview_provider_signature`, `_synthesize_voice_preview`, and `_get_reusable_full_voice_preview`.
- Produces: `get_current_narration_fingerprint(params, voice_mode) -> str`.
- Produces: `confirm_script_and_build_timeline(params, voice_mode, app_config_snapshot) -> SixClipPlan | None`.
- Stores in Streamlit session: one full preview carrying `fingerprint`, exact `duration`, `sub_maker`, and audio bytes.

- [ ] **Step 1: Write failing tests proving generation is text-only and confirmation is explicit**

Use `AppTest` and mocks to assert:

```python
def test_generate_script_does_not_call_tts_or_build_timeline():
    with patch.object(voice, "tts") as tts, patch.object(
        six_clip_plan, "generate_six_clip_plan"
    ) as analyze:
        click_generate_script()
    tts.assert_not_called()
    analyze.assert_not_called()

def test_confirm_builds_one_timeline_from_one_full_tts_call():
    with patch.object(voice, "tts", return_value=sub_maker) as tts:
        click_confirm_script_and_build_timeline()
    tts.assert_called_once()
    assert app.session_state["six_clip_plan"]["narration_duration_sec"] == 63.0
    assert len(app.session_state["six_clip_plan"]["segments"]) == 7
```

Add tests that a second click with the identical full fingerprint reuses the cached audio rather than calling TTS again; script, provider, voice, rate, volume, or provider-signature changes produce a different fingerprint; prompt/media edits do not.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_webui_voice_preview.py test/services/test_six_clip_webui.py`

Expected: FAIL because script generation currently also creates a six-clip plan and the explicit confirmation orchestration does not exist.

- [ ] **Step 3: Make Generate Script text-only**

In `_render_local_script_generation`, return only the generated script and existing harmless keyword display data; remove `generate_six_clip_plan` and `set_session_plan` from this button path. Applying a LoomLoom script candidate likewise changes only text and naturally marks any existing timeline stale through fingerprint comparison.

- [ ] **Step 4: Make full confirmation audio safe for final reuse at every selected volume**

Full confirmation must synthesize canonical narration at provider volume `1.0` because final MoviePy muxing applies `params.voice_volume`. Keep the selected volume in the fingerprint so changing it still requires reconfirmation, but do not bake the gain into the cached file. Update the old non-default-volume test: matching canonical full audio is reused and the final mixer applies gain once.

```python
tts_volume = 1.0 if preview_type == "full" else voice_volume
sub_maker = voice.tts(
    text=content,
    voice_name=voice_name,
    voice_rate=voice_rate,
    voice_file=audio_file,
    voice_volume=tts_volume,
)
```

- [ ] **Step 5: Implement the explicit confirmation button**

Label the action `Confirm Script & Build Timeline` and state that it may call a paid TTS provider. On click:

1. reject an empty script;
2. compute the current full fingerprint;
3. reuse an identical valid full preview or synthesize once;
4. validate exact finite positive duration;
5. calculate ranges using `config.app.get("max_dynamic_clip_count", 0)`;
6. normalize subtitle cues;
7. call the existing LLM path with the exact ranges;
8. set the new plan only after all validation succeeds;
9. keep the previous plan visible and stale if TTS or LLM fails.

- [ ] **Step 6: Display authoritative confirmation state**

Show the local duration range as `Estimated` before confirmation. After confirmation show exact measured seconds, required clip count, and `Current`. If the fingerprint differs or the audio/timing payload is absent, show `Timeline is stale—reconfirm before rendering` and retain the prior plan/media.

- [ ] **Step 7: Run focused tests and Ruff; confirm GREEN**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_webui_voice_preview.py test/services/test_six_clip_webui.py test/services/test_six_clip_ai.py`

Run: `uv run --no-sync ruff check webui/Main.py webui/six_clip_timeline.py test/services/test_webui_voice_preview.py test/services/test_six_clip_webui.py`

Expected: all commands PASS and mock TTS call count is exactly one for an uncached confirmation.

- [ ] **Step 8: Commit Task 3**

```bash
git add webui/Main.py webui/six_clip_timeline.py test/services/test_webui_voice_preview.py test/services/test_six_clip_webui.py
git commit -m "feat: confirm narration before building timeline"
```

---

### Task 4: Render dynamic cards, preserve range-bound media, and paginate by six

**Files:**
- Modify: `webui/six_clip_timeline.py:18-352`
- Modify: `webui/Main.py:5574-5591`
- Modify: `test/services/test_six_clip_webui.py`

**Interfaces:**
- Produces: `merge_media_for_unchanged_ranges(previous: SixClipPlan | None, rebuilt: SixClipPlan) -> SixClipPlan`.
- Produces: `timeline_page(plan: SixClipPlan, page: int, page_size: int = 6) -> tuple[list[SixClipSegment], int]`.
- Consumes: `build_master_prompt_batches(plan, batch_size=6)`.

- [ ] **Step 1: Write failing pagination and media-retention tests**

```python
def test_timeline_page_uses_absolute_indexes():
    visible, page_count = timeline_page(dynamic_plan(13), page=2)
    assert page_count == 3
    assert [item.index for item in visible] == [7, 8, 9, 10, 11, 12]

def test_rebuild_keeps_media_only_for_identical_ranges():
    merged = merge_media_for_unchanged_ranges(plan_63_seconds(), plan_68_seconds())
    assert merged.segments[:6] == media_from_old_first_six()
    assert merged.segments[6].media_path == ""
```

Also test that reducing from 13 to 7 does not reassign Clip 8–13 media and that changing title/prompt/media leaves confirmation current.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_webui.py`

Expected: FAIL because all segments currently render on one page and rebuild retention is undefined.

- [ ] **Step 3: Implement pure pagination and exact-range media merge**

Use the tuple `(index, start_sec, end_sec)` as the retention key. Preserve only `media_kind/media_path` for identical keys whose local files still exist. Never move media from one range to another.

- [ ] **Step 4: Update Section 2 and Section 3**

Rename the heading to `Section 2 — Timeline Clips`, show `N clips / X seconds`, render only six cards on the selected page, and retain absolute widget keys such as `six_clip_13_prompt`. Render each master-prompt batch in its own copyable `st.code` block.

- [ ] **Step 5: Remove automatic refresh behavior that bypasses narration confirmation**

Delete or disable `Generate / Refresh 6 Clip Prompts with AI` as an independent plan-building path. The only range-changing action is confirmation/rebuild; prompt fields remain manually editable without invalidating narration.

- [ ] **Step 6: Run focused tests and Ruff; confirm GREEN**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_webui.py test/services/test_six_clip_history.py`

Run: `uv run --no-sync ruff check webui/six_clip_timeline.py webui/Main.py test/services/test_six_clip_webui.py`

Expected: all commands PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add webui/six_clip_timeline.py webui/Main.py test/services/test_six_clip_webui.py
git commit -m "feat: paginate dynamic timeline clips"
```

---

### Task 5: Generalize safe media import and task materialization

**Files:**
- Modify: `app/services/six_clip_media.py:97-103,207-281`
- Modify: `test/services/test_six_clip_media.py:63-220`
- Modify: `test/services/test_six_clip_history.py`

**Interfaces:**
- Preserves: `import_media_url`, `save_uploaded_media`, `validate_ready_media`, `missing_media_message`, and `materialize_plan_for_task`.
- Changes filename format to stable three-digit absolute indexes such as `clip-001.mp4`.

- [ ] **Step 1: Write failing arbitrary-index and missing-range tests**

```python
def test_import_accepts_dynamic_absolute_index(monkeypatch, tmp_path, mp4_bytes):
    imported = six_clip_media.import_media_url(
        "https://example.com/media",
        tmp_path,
        clip_index=127,
    )
    assert imported.local_path.endswith("clip-127.mp4")

def test_missing_media_lists_dynamic_absolute_ranges(tmp_path):
    message = six_clip_media.missing_media_message(plan_with_missing(7, 13))
    assert "Clip 7 (60–70s)" in message
    assert "Clip 13 (120–127s)" in message
```

Test `clip_index=0` rejection, signed-query redaction, and task-local filenames for 13 segments.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_media.py test/services/test_six_clip_history.py`

Expected: FAIL because `_destination_path` rejects indexes above six and uses two-digit names.

- [ ] **Step 3: Remove the fixed upper bound and use stable names**

Require `clip_index >= 1`. Use `clip-{clip_index:03d}` in session imports and task materialization. Keep content magic, byte limits, streamed download, timeouts, and sanitized error behavior unchanged.

- [ ] **Step 4: Run focused tests and Ruff; confirm GREEN**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_media.py test/services/test_six_clip_history.py`

Run: `uv run --no-sync ruff check app/services/six_clip_media.py test/services/test_six_clip_media.py test/services/test_six_clip_history.py`

Expected: all commands PASS and no raw signed query appears in failures.

- [ ] **Step 5: Commit Task 5**

```bash
git add app/services/six_clip_media.py test/services/test_six_clip_media.py test/services/test_six_clip_history.py
git commit -m "feat: accept media for dynamic timeline indexes"
```

---

### Task 6: Render arbitrary timelines and verify exact output duration

**Files:**
- Modify: `app/services/six_clip_render.py:13-193`
- Modify: `test/services/test_six_clip_render.py:1-124`
- Modify only if a tested adapter is required: `app/services/video.py:332-393`

**Interfaces:**
- Preserves: `prepare_six_clip_timeline(...) -> list[str]`.
- Changes: `concat_six_clip_timeline(clip_paths, output_file, *, timeline_duration_sec: float, threads: int = 2, duration_tolerance_sec: float = 0.5) -> str`.
- Produces: `probe_video_duration(path: str) -> float` local to `six_clip_render.py`.

- [ ] **Step 1: Write failing 63-second, 127-second, and mismatch tests**

```python
@pytest.mark.parametrize(("duration", "count"), [(63.0, 7), (127.0, 13)])
def test_concat_uses_dynamic_plan_duration(monkeypatch, tmp_path, duration, count):
    clips = make_prepared_clips(tmp_path, count)
    monkeypatch.setattr(six_clip_render, "probe_video_duration", lambda _: duration)
    six_clip_render.concat_six_clip_timeline(
        clips,
        tmp_path / "combined.mp4",
        timeline_duration_sec=duration,
    )
    assert captured["clip_files"] == clips
    assert captured["max_duration"] == duration

def test_concat_rejects_duration_outside_half_second(monkeypatch, tmp_path):
    monkeypatch.setattr(six_clip_render, "probe_video_duration", lambda _: 61.9)
    with pytest.raises(SixClipRenderError, match="expected 63.0"):
        concat_for_63_seconds(tmp_path)
```

Assert seven normalized 10-second sources are passed in order and the concat cap trims the final source to three seconds. Assert no source list cycling occurs.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_render.py`

Expected: FAIL because preparation and concat require exactly six items and cap at 60 seconds.

- [ ] **Step 3: Generalize preparation and concat**

Iterate over validated plan segments without a fixed count. Continue normalizing each source to 10 seconds, stripping source audio and looping short sources. Pass `plan.timeline_duration_sec` into concat so the last prepared source is trimmed to the partial final range.

- [ ] **Step 4: Add duration probing and fail-closed validation**

Open the combined output with the existing quiet MoviePy helper, read a finite positive duration, close the clip in `finally`, and raise `SixClipRenderError` when `abs(actual - expected) > 0.5`. Keep artifacts for diagnosis.

- [ ] **Step 5: Run focused video regressions and Ruff; confirm GREEN**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_render.py test/services/test_video.py test/services/test_image_materials.py test/services/test_clip_speed.py`

Run: `uv run --no-sync ruff check app/services/six_clip_render.py test/services/test_six_clip_render.py`

Expected: all commands PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add app/services/six_clip_render.py test/services/test_six_clip_render.py
git commit -m "feat: render narration length timelines"
```

---

### Task 7: Enforce current confirmation and plan/audio agreement in the pipeline

**Files:**
- Modify: `app/services/task.py:394-521,1199-1400`
- Modify: `webui/Main.py:5220-5520`
- Modify: `test/services/test_six_clip_task_pipeline.py:1-167`
- Modify: `test/services/test_webui_voice_preview.py:340-490`

**Interfaces:**
- Produces: `measure_timeline_audio_duration(audio_file: str, voice_preview: dict | None) -> float`.
- Produces: `validate_audio_matches_plan(plan: SixClipPlan, measured_duration_sec: float) -> None`.
- Consumes: preview `fingerprint` equal to `plan.narration_fingerprint`.

- [ ] **Step 1: Replace the old over-60 failure test with failing agreement tests**

```python
def test_sixty_three_second_narration_uses_seven_clip_plan(monkeypatch, tmp_path):
    params = params_for(plan_63_seconds(tmp_path))
    monkeypatch.setattr(task, "generate_audio", lambda *a, **k: ("audio.mp3", 64, cues))
    monkeypatch.setattr(task.voice, "get_audio_duration", lambda _: 63.0)
    result = task._run_pipeline("task-63", params, voice_preview=matching_preview())
    assert result["error"] is None
    assert render_call.timeline_duration_sec == 63.0

def test_backend_rejects_plan_audio_range_mismatch_before_subtitle(monkeypatch):
    params = params_for(plan_63_seconds())
    monkeypatch.setattr(task.voice, "get_audio_duration", lambda _: 68.0)
    with assert_subtitle_not_called():
        result = task._run_pipeline("mismatch", params)
    assert result["failed_stage"] == "audio"
    assert "confirm/rebuild timeline" in result["error"]
```

Add tests that stale fingerprint and missing reusable payload block WebUI submission before materialization; an API/backend call without preview synthesizes exactly once, then validates ranges; no stock downloader is called for 7 or 13 clips.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_task_pipeline.py test/services/test_webui_voice_preview.py`

Expected: FAIL because the pipeline rejects durations above 60 and does not compare dynamic ranges/fingerprints.

- [ ] **Step 3: Strengthen WebUI preflight before task submission**

Before copying media or submitting:

1. require a plan;
2. recompute the current narration fingerprint;
3. require `is_timeline_current`;
4. require `_get_reusable_full_voice_preview` with audio bytes, exact duration, subtitle timing object, and the matching fingerprint;
5. require all plan media;
6. only then materialize files and submit.

A stale/missing cache shows `Confirm/Rebuild Timeline before generating video`. It must not silently fall back to task-side TTS.

- [ ] **Step 4: Preserve exact preview metadata in the task payload**

Include `fingerprint` in `_get_reusable_full_voice_preview` and in the task-local copied preview. Update `_resolve_reusable_voice_preview` to return the exact float duration instead of `math.ceil(duration)` while keeping script/voice/rate/volume and task-local path checks.

- [ ] **Step 5: Replace the fixed 60-second rejection**

After audio preparation, measure exact duration from the actual file or validated preview, call `build_timeline_ranges` with the configured maximum, compare count/ranges/timeline duration to the submitted plan within `1e-6`, and fail before subtitle/render when mismatched. Pass `params.six_clip_plan.timeline_duration_sec` to `concat_six_clip_timeline`.

- [ ] **Step 6: Preserve backend/API behavior without hidden repeated synthesis**

When no reusable preview is supplied, allow the existing TTS path to synthesize once, then validate the measured result against the submitted plan. Do not change voice rate, truncate narration, rebuild the plan automatically, or call a second TTS request.

- [ ] **Step 7: Run pipeline, task, preview, and no-stock regressions**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_task_pipeline.py test/services/test_webui_voice_preview.py test/services/test_task.py test/services/test_webui_task.py`

Run: `uv run --no-sync ruff check app/services/task.py webui/Main.py test/services/test_six_clip_task_pipeline.py test/services/test_webui_voice_preview.py`

Expected: all commands PASS and the removed error text `fixed at 60 seconds` is absent from `app/services/task.py`.

- [ ] **Step 8: Commit Task 7**

```bash
git add app/services/task.py webui/Main.py test/services/test_six_clip_task_pipeline.py test/services/test_webui_voice_preview.py
git commit -m "feat: validate confirmed timeline before render"
```

---

### Task 8: Restore legacy and dynamic history safely

**Files:**
- Modify: `webui/six_clip_timeline.py:50-126`
- Modify: `webui/Main.py:1129-1280`
- Modify if serialization needs an explicit adapter: `app/services/task_artifacts.py`
- Modify: `test/services/test_six_clip_history.py:1-121`

**Interfaces:**
- Persists through existing `VideoParams.model_dump`: exact duration, timeline duration, slot duration, fingerprint, all segments, and local media paths.
- Restores a plan for review but never restores the in-memory audio bytes or `sub_maker`.

- [ ] **Step 1: Write failing legacy/dynamic restoration tests**

```python
def test_dynamic_history_restores_thirteen_segments_and_exact_metadata(tmp_path):
    restored = restore_plan_from_task_params(saved_dynamic_params(tmp_path, 127.0))
    assert len(restored.segments) == 13
    assert restored.narration_duration_sec == 127.0
    assert restored.timeline_duration_sec == 127.0
    assert restored.narration_fingerprint == "saved-fingerprint"

def test_restored_confirmation_is_stale_without_memory_cache():
    restore_dynamic_task()
    assert timeline_status() == "stale"
    assert generate_video_is_disabled()
```

Keep legacy six-plan load and missing-file-clears-media tests. Assert raw history contains no `Signature=`, `X-Goog-Signature=`, API key, or remote URL.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_history.py test/services/test_webui_task_history.py`

Expected: FAIL until dynamic metadata and stale-on-restore behavior are wired.

- [ ] **Step 3: Restore all segments and clear only invalid media attachments**

Keep titles, narration contexts, prompts, indexes, and exact ranges. Clear `media_kind/media_path` only when the referenced local file is absent or empty. Do not synthesize audio, regenerate prompts, or attach stock media during restoration.

- [ ] **Step 4: Mark restored narration confirmation stale**

Retain the saved fingerprint for audit/comparison, but require the in-memory matching full preview and timing object before `Current` status or final submission. Show the rebuild action with no automatic TTS call.

- [ ] **Step 5: Run history regressions and Ruff; confirm GREEN**

Run: `uv run --no-sync python -X utf8 -m pytest -q test/services/test_six_clip_history.py test/services/test_webui_task_history.py test/services/test_task_artifacts.py`

Run: `uv run --no-sync ruff check webui/six_clip_timeline.py webui/Main.py test/services/test_six_clip_history.py`

Expected: all commands PASS.

- [ ] **Step 6: Commit Task 8**

```bash
git add webui/six_clip_timeline.py webui/Main.py test/services/test_six_clip_history.py
git commit -m "feat: restore dynamic timeline history safely"
```

---

### Task 9: Full verification, Windows smoke, and review handoff

**Files:**
- Modify: `.github/workflows/ci.yml` Windows smoke test list
- Modify only when verification exposes a defect: the owning production file and its focused test
- No merge files or temporary workflows

**Interfaces:**
- Release evidence: focused dynamic suite, complete suite with branch coverage, Ruff, compile, Python 3.11/3.13 CI, and Windows smoke.

- [ ] **Step 1: Add dynamic tests to Windows smoke**

Append these mocked/no-network suites to the existing Windows command:

```yaml
test/services/test_six_clip_plan.py
test/services/test_six_clip_render.py
test/services/test_six_clip_task_pipeline.py
test/services/test_six_clip_history.py
```

- [ ] **Step 2: Run the complete focused dynamic suite**

Run:

```bash
uv run --no-sync python -X utf8 -m pytest -q \
  test/services/test_six_clip_plan.py \
  test/services/test_six_clip_ai.py \
  test/services/test_six_clip_media.py \
  test/services/test_six_clip_render.py \
  test/services/test_six_clip_task_pipeline.py \
  test/services/test_six_clip_webui.py \
  test/services/test_six_clip_history.py \
  test/services/test_webui_voice_preview.py
```

Expected: PASS with no real TTS, LLM, stock, or remote-media call.

- [ ] **Step 3: Run compile and Ruff**

Run: `uv run --no-sync python -m compileall app cli.py main.py webui test`

Run: `uv run --no-sync ruff check app cli.py main.py webui test`

Expected: both PASS.

- [ ] **Step 4: Run the full coverage suite**

Run: `uv run --no-sync python -X utf8 -m coverage run -m pytest -q test`

Run: `uv run --no-sync python -m coverage report`

Expected: all tests PASS and branch coverage remains at or above 70%.

- [ ] **Step 5: Push and inspect CI on Draft PR #3**

Require green `Python 3.11 tests`, `Python 3.13 tests`, and `Windows smoke tests`. If GitHub does not start jobs because of billing/spending limits, report that as an external blocker; do not describe CI as passing and do not merge.

- [ ] **Step 6: Perform manual acceptance with non-charge and charge boundaries**

Verify locally without paid calls first: legacy 60-second history, stale warning, pagination, missing-media lock, and 63/127 mocked plans. Then, only with user-authorized provider credentials, perform one real confirmation and confirm the final task reuses the same audio without a second TTS charge.

- [ ] **Step 7: Commit the CI update and any verified defect fixes separately**

```bash
git add .github/workflows/ci.yml
git commit -m "test: cover dynamic timeline on windows"
```

- [ ] **Step 8: Keep Draft PR #3 unmerged and provide Windows download testing instructions**

Report commit SHAs, exact commands/results, any external blockers, and the user-facing steps to download the branch into a new Windows folder. Do not merge PR #3 or PR #4.

---

## Spec coverage self-review

- Approved text-only generation and explicit paid confirmation: Task 3.
- Exact duration, range formula, configured safety limit, and legacy defaults: Task 1.
- Subtitle cue use, LLM fallback, exact dynamic output, partial-slot instruction, and six-item prompt batches: Task 2.
- Six-card pages, absolute keys, stale display, and range-bound media retention: Tasks 3–4.
- Arbitrary media indexes, redaction, no stock fallback, and task-local paths: Tasks 5 and 7.
- Ordered FFmpeg rendering, partial final trim, no cycling, dynamic duration cap, and ±0.5-second verification: Task 6.
- Fingerprint/cache reuse, backend one-synthesis fallback, plan/audio agreement, and removal of the over-60 rejection: Task 7.
- Legacy/dynamic history, missing files, and stale restoration: Task 8.
- Python 3.11/3.13, Ruff, compile, Windows smoke, coverage, and no-merge gate: Task 9.
- Cloud Agent Flow/Canva changes remain outside this baseline plan; PR #4 stays separate.
