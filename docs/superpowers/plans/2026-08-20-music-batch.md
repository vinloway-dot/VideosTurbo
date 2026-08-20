# Music Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-ready Music Batch mode to VideosTurbo that can create one stock-footage video per song, resume interrupted batches, optionally combine completed videos, and preserve every existing MoneyPrinterTurbo workflow unchanged.

**Architecture:** Implement Music Batch as a new orchestration layer above the existing task/render services. Keep the normal Video Generator, API, CLI, provider integrations, TTS, subtitle, BGM, encoder behavior, and configuration intact; add focused batch modules plus a separate Streamlit UI module, with only minimal integration changes to `webui/Main.py`.

**Tech Stack:** Python 3.11+, Pydantic, Streamlit 1.59.1, MoviePy 2.2.1, FFmpeg, pytest 9.1.1, coverage 7.15.1, ruff 0.15.21, existing MoneyPrinterTurbo task/material/video services.

**Spec:** `docs/superpowers/specs/2026-08-20-music-batch-design.md`

## Global Constraints

- Existing functionality must not be removed, replaced, or reduced.
- Existing Video Generator, API, CLI, TTS, subtitle, BGM, provider, encoder, and configuration behavior must remain regression-compatible.
- Music Batch default output is 1920x1080 / 16:9, but existing supported aspect ratios remain selectable.
- Supported batch audio inputs: `.mp3`, `.wav`, `.m4a`, `.flac`.
- Retry count default: `2` retries after the initial attempt.
- Parallel jobs default: `1`; UI maximum: `4`; warn above `1`.
- `Avoid reusing clips in this batch` default: OFF and best-effort only.
- `Combine all videos after batch` is explicit and user-controlled.
- Never silently launch a full compilation re-encode after stream-copy incompatibility; require user approval.
- Do not silently downgrade an intended long `h264_nvenc` batch to CPU without surfacing it.
- Do not lower the existing coverage threshold (`70`) or weaken existing tests.
- Prefer new focused files; keep changes to `webui/Main.py` minimal.

---

## File Structure Locked for Implementation

Create:
- `app/services/music_batch/__init__.py` — public batch-service exports.
- `app/services/music_batch/models.py` — batch/song config and status models.
- `app/services/music_batch/input.py` — audio discovery, normalization, natural sorting, output naming.
- `app/services/music_batch/state.py` — atomic durable state store and interrupted-state recovery.
- `app/services/music_batch/sources.py` — multi-provider request planning and used-clip registry.
- `app/services/music_batch/concat.py` — ffprobe compatibility checks and concat execution.
- `app/services/music_batch/preflight.py` — input/output/ffmpeg/encoder/API-key/disk checks.
- `app/services/music_batch/manager.py` — orchestration, retries, resume, parallel scheduling, reports.
- `webui/music_batch.py` — isolated Streamlit Music Batch page.
- `test/services/music_batch/test_models.py`
- `test/services/music_batch/test_input.py`
- `test/services/music_batch/test_state.py`
- `test/services/music_batch/test_sources.py`
- `test/services/music_batch/test_concat.py`
- `test/services/music_batch/test_preflight.py`
- `test/services/music_batch/test_manager.py`
- `test/services/music_batch/test_integration.py`
- `test/test_music_batch_webui.py`

Modify only when required:
- `webui/Main.py` — register/render the new Music Batch mode with minimal surface change.
- `app/services/material.py` — only if a narrow hook is needed to return stable provider identifiers to Music Batch; do not change normal single-video behavior.
- `app/services/task.py` — only if a narrow reusable sync/invocation seam is needed; preserve all current callers.
- `README-en.md` and `README.md` — document the additive Music Batch feature and local workflow after implementation is verified.

---

### Task 1: Batch Models and Settings Resolution

**Files:**
- Create: `app/services/music_batch/__init__.py`
- Create: `app/services/music_batch/models.py`
- Create: `test/services/music_batch/test_models.py`

**Interfaces:**
- Produces: `SongStatus`, `BatchStatus`, `SortMode`, `SongOverride`, `BatchSettings`, `SongItem`, `BatchState`.
- Produces: `resolve_song_settings(batch_settings: BatchSettings, song: SongItem) -> dict[str, object]`.
- Consumed later by input/state/manager/web UI tasks.

- [ ] **Step 1: Write failing model/default tests**

```python
from app.services.music_batch.models import BatchSettings, SongItem, SongOverride, SortMode, resolve_song_settings


def test_batch_defaults_match_spec():
    cfg = BatchSettings(output_root="D:/out")
    assert cfg.video_aspect == "16:9"
    assert cfg.retry_count == 2
    assert cfg.parallel_jobs == 1
    assert cfg.avoid_reusing_clips is False
    assert cfg.sort_mode == SortMode.filename


def test_song_override_replaces_only_explicit_fields():
    cfg = BatchSettings(
        output_root="D:/out",
        video_script="global script",
        video_keywords=["ocean"],
        stock_sources=["pexels", "pixabay"],
        video_clip_duration=8,
    )
    song = SongItem(
        source_path="D:/music/a.mp3",
        added_index=0,
        override=SongOverride(video_keywords=["forest"], video_clip_duration=10),
    )
    resolved = resolve_song_settings(cfg, song)
    assert resolved["video_script"] == "global script"
    assert resolved["video_keywords"] == ["forest"]
    assert resolved["stock_sources"] == ["pexels", "pixabay"]
    assert resolved["video_clip_duration"] == 10
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest test/services/music_batch/test_models.py -v`

Expected: FAIL because `app.services.music_batch.models` does not exist.

- [ ] **Step 3: Implement the model layer**

Use Pydantic models/enums. Required validation:

```python
class BatchSettings(BaseModel):
    output_root: str
    video_script: str = ""
    video_keywords: list[str] = []
    stock_sources: list[str] = ["pexels"]
    video_aspect: str = "16:9"
    video_concat_mode: str = "random"
    video_transition_mode: str | None = None
    video_clip_duration: int = Field(default=8, ge=1)
    video_clip_speed: float = Field(default=1.0, gt=0)
    video_encoder: str = "libx264"
    retry_count: int = Field(default=2, ge=0, le=10)
    parallel_jobs: int = Field(default=1, ge=1, le=4)
    sort_mode: SortMode = SortMode.filename
    avoid_reusing_clips: bool = False
    combine_all: bool = False
```

Use `Field(default_factory=list)` for mutable lists in final code. `SongOverride` fields are optional; `resolve_song_settings` overlays only non-`None` override values.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `uv run pytest test/services/music_batch/test_models.py -v`

Expected: PASS.

- [ ] **Step 5: Run lint for touched files**

Run: `uv run ruff check app/services/music_batch/models.py test/services/music_batch/test_models.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/music_batch test/services/music_batch/test_models.py
git commit -m "feat: add music batch models"
```

---

### Task 2: Audio Discovery, Natural Sorting, and Safe Output Naming

**Files:**
- Create: `app/services/music_batch/input.py`
- Create: `test/services/music_batch/test_input.py`

**Interfaces:**
- Consumes: `SongItem`, `SortMode` from Task 1.
- Produces: `SUPPORTED_AUDIO_EXTENSIONS`, `discover_audio_files(folder: Path, include_subfolders: bool) -> list[Path]`.
- Produces: `normalize_uploaded_paths(paths: Sequence[Path]) -> list[Path]`.
- Produces: `sort_song_items(items: list[SongItem], mode: SortMode) -> list[SongItem]`.
- Produces: `allocate_output_path(batch_dir: Path, source_audio: Path) -> Path`.

- [ ] **Step 1: Write failing discovery/sort/naming tests**

```python
def test_discover_audio_files_respects_subfolder_flag(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "ignore.txt").write_text("x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.wav").write_bytes(b"x")

    assert [p.name for p in discover_audio_files(tmp_path, False)] == ["a.mp3"]
    assert {p.name for p in discover_audio_files(tmp_path, True)} == {"a.mp3", "b.wav"}


def test_filename_sort_is_natural():
    items = [
        SongItem(source_path="10.mp3", added_index=0),
        SongItem(source_path="2.mp3", added_index=1),
    ]
    ordered = sort_song_items(items, SortMode.filename)
    assert [Path(x.source_path).name for x in ordered] == ["2.mp3", "10.mp3"]


def test_allocate_output_path_never_overwrites(tmp_path):
    first = allocate_output_path(tmp_path, Path("Calm Ocean.mp3"))
    first.touch()
    second = allocate_output_path(tmp_path, Path("Calm Ocean.mp3"))
    assert second.name == "Calm Ocean_2.mp4"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/music_batch/test_input.py -v`

- [ ] **Step 3: Implement input helpers**

Required behavior: extensions are case-insensitive; duplicate paths are normalized with `Path.resolve()` when possible; added order remains stable; folder scan default is non-recursive; filenames are sanitized only enough to remain valid on the current OS without changing user-visible names unnecessarily.

- [ ] **Step 4: Run GREEN and lint**

Run:

```bash
uv run pytest test/services/music_batch/test_input.py -v
uv run ruff check app/services/music_batch/input.py test/services/music_batch/test_input.py
```

- [ ] **Step 5: Commit**

```bash
git add app/services/music_batch/input.py test/services/music_batch/test_input.py
git commit -m "feat: add music batch input discovery"
```

---

### Task 3: Atomic Batch State, Resume, Start Over, and Retry-Failed Recovery

**Files:**
- Create: `app/services/music_batch/state.py`
- Create: `test/services/music_batch/test_state.py`

**Interfaces:**
- Consumes: `BatchState`, `SongStatus`, `BatchStatus`.
- Produces: `BatchStateStore(batch_dir: Path)` with `save(state)`, `load()`, `mutate(fn)`, `recover_interrupted()`, and `retry_failed()`.
- Produces: `make_restart_directory(previous: Path) -> Path`.

- [ ] **Step 1: Write failing atomicity and resume tests**

```python
def test_save_uses_atomic_replace(tmp_path, monkeypatch):
    store = BatchStateStore(tmp_path)
    state = BatchState.new_for_test()
    store.save(state)
    assert (tmp_path / "batch_state.json").exists()
    assert not (tmp_path / "batch_state.json.tmp").exists()


def test_recover_interrupted_returns_processing_to_pending(tmp_path):
    state = BatchState.new_for_test(status="processing")
    state.songs[0].status = SongStatus.processing
    store = BatchStateStore(tmp_path)
    store.save(state)
    recovered = store.recover_interrupted()
    assert recovered.songs[0].status == SongStatus.pending


def test_retry_failed_only_resets_failed_songs(tmp_path):
    state = BatchState.new_for_test(song_statuses=[SongStatus.completed, SongStatus.failed])
    store = BatchStateStore(tmp_path)
    store.save(state)
    retried = store.retry_failed()
    assert retried.songs[0].status == SongStatus.completed
    assert retried.songs[1].status == SongStatus.pending
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/music_batch/test_state.py -v`

- [ ] **Step 3: Implement state store with locking and atomic write-then-replace**

Use a process-local `threading.RLock` around mutation and write JSON to a sibling temporary file, `flush`, `os.fsync`, then `os.replace`. State-write failure must propagate as a fatal exception rather than being swallowed.

- [ ] **Step 4: Add restart-directory collision test and implementation**

Expected names: `batch_x_restart_01`, `_02`, etc.; never delete the previous run.

- [ ] **Step 5: Run GREEN and lint**

```bash
uv run pytest test/services/music_batch/test_state.py -v
uv run ruff check app/services/music_batch/state.py test/services/music_batch/test_state.py
```

- [ ] **Step 6: Commit**

```bash
git add app/services/music_batch/state.py test/services/music_batch/test_state.py
git commit -m "feat: add durable music batch state"
```

---

### Task 4: Multi-Source Planning and Best-Effort Clip Reuse Avoidance

**Files:**
- Create: `app/services/music_batch/sources.py`
- Create: `test/services/music_batch/test_sources.py`
- Modify only if tests prove necessary: `app/services/material.py`

**Interfaces:**
- Produces: `SourcePlan(provider: str, keywords: list[str], requested_duration: float)`.
- Produces: `build_source_plan(sources: list[str], keywords: list[str], target_duration: float) -> list[SourcePlan]`.
- Produces: `UsedClipRegistry` with `seen(provider, clip_id)`, `mark(provider, clip_id)`, `filter_candidates(...)`.
- Manager later calls existing provider/material services according to the plan.

- [ ] **Step 1: Write failing round-robin/distribution tests**

```python
def test_build_source_plan_distributes_duration_across_selected_sources():
    plans = build_source_plan(["pexels", "pixabay", "coverr"], ["ocean", "forest"], 180)
    assert [p.provider for p in plans] == ["pexels", "pixabay", "coverr"]
    assert sum(p.requested_duration for p in plans) == pytest.approx(180)


def test_used_clip_registry_filters_seen_but_can_fallback():
    registry = UsedClipRegistry()
    registry.mark("pexels", "123")
    candidates = [("123", "a"), ("456", "b")]
    assert registry.filter_candidates("pexels", candidates, avoid_reuse=True) == [("456", "b")]
    assert registry.filter_candidates("pexels", [("123", "a")], avoid_reuse=True) == [("123", "a")]
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/music_batch/test_sources.py -v`

- [ ] **Step 3: Implement source planning without changing normal `video_source` semantics**

The batch layer owns multi-source orchestration. Do not teach the existing single-video path a new multi-source meaning.

- [ ] **Step 4: If stable provider IDs are unavailable, add the smallest material-service hook**

Any hook must preserve current return data for existing callers and add stable ID/source metadata only through a backward-compatible optional path.

- [ ] **Step 5: Run provider regression subset plus new tests**

Run:

```bash
uv run pytest test/services/music_batch/test_sources.py test/services -q
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/music_batch/sources.py test/services/music_batch/test_sources.py app/services/material.py
git commit -m "feat: add batch stock source orchestration"
```

If `app/services/material.py` was not changed, omit it from `git add`.

---

### Task 5: Preflight Validation Including FFmpeg and NVENC Visibility

**Files:**
- Create: `app/services/music_batch/preflight.py`
- Create: `test/services/music_batch/test_preflight.py`

**Interfaces:**
- Produces: `PreflightIssue(level: Literal["warning", "error"], code: str, message: str)`.
- Produces: `run_preflight(settings: BatchSettings, songs: list[SongItem]) -> list[PreflightIssue]`.
- Produces: `probe_encoder(codec: str) -> tuple[bool, str]` using a short FFmpeg lavfi encode probe.

- [ ] **Step 1: Write failing tests for missing inputs/output/ffmpeg/encoder**

```python
def test_preflight_requires_song():
    issues = run_preflight(BatchSettings(output_root="D:/out"), [])
    assert any(i.code == "no_inputs" and i.level == "error" for i in issues)


def test_nvenc_failure_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "probe_encoder", lambda codec: (False, "nvenc unavailable"))
    settings = BatchSettings(output_root=str(tmp_path), video_encoder="h264_nvenc")
    issues = run_preflight(settings, [SongItem(source_path=__file__, added_index=0)])
    assert any(i.code == "encoder_unavailable" and "h264_nvenc" in i.message for i in issues)
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/music_batch/test_preflight.py -v`

- [ ] **Step 3: Implement preflight checks**

Validate: readable inputs; writable/createable output; FFmpeg executable; selected encoder probe; configured API keys for each selected online source; disk-space warning via `shutil.disk_usage` without brittle exact-size prediction.

- [ ] **Step 4: Run GREEN and lint**

```bash
uv run pytest test/services/music_batch/test_preflight.py -v
uv run ruff check app/services/music_batch/preflight.py test/services/music_batch/test_preflight.py
```

- [ ] **Step 5: Commit**

```bash
git add app/services/music_batch/preflight.py test/services/music_batch/test_preflight.py
git commit -m "feat: add music batch preflight checks"
```

---

### Task 6: Compilation Compatibility, Stream Copy, and Explicit Re-encode Path

**Files:**
- Create: `app/services/music_batch/concat.py`
- Create: `test/services/music_batch/test_concat.py`

**Interfaces:**
- Produces: `MediaSignature` dataclass/Pydantic model.
- Produces: `probe_media_signature(path: Path) -> MediaSignature` using `ffprobe -show_streams -of json`.
- Produces: `are_stream_copy_compatible(paths: Sequence[Path]) -> tuple[bool, str]`.
- Produces: `concat_stream_copy(paths: Sequence[Path], output: Path) -> Path`.
- Produces: `concat_reencode(paths: Sequence[Path], output: Path, codec: str) -> Path`, called only after explicit approval in UI/manager.

- [ ] **Step 1: Write failing compatibility tests**

```python
def test_compatibility_requires_matching_video_and_audio_signatures(monkeypatch):
    signatures = {
        "a.mp4": MediaSignature("h264", 1920, 1080, "30/1", "aac", 48000, "stereo"),
        "b.mp4": MediaSignature("h264", 1920, 1080, "30/1", "aac", 48000, "stereo"),
    }
    monkeypatch.setattr(concat, "probe_media_signature", lambda p: signatures[p.name])
    ok, reason = are_stream_copy_compatible([Path("a.mp4"), Path("b.mp4")])
    assert ok is True
    assert reason == "compatible"
```

Add a mismatch test that returns `False` and a concrete reason.

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/music_batch/test_concat.py -v`

- [ ] **Step 3: Implement ffprobe parsing and safe concat-list generation**

Use a temporary concat list with escaped absolute paths, run `ffmpeg -f concat -safe 0 -i <list> -c copy <output>` for compatible streams.

- [ ] **Step 4: Implement explicit re-encode function but do not auto-call it**

The manager returns a `needs_reencode_confirmation` state when compatibility fails.

- [ ] **Step 5: Run GREEN and lint**

```bash
uv run pytest test/services/music_batch/test_concat.py -v
uv run ruff check app/services/music_batch/concat.py test/services/music_batch/test_concat.py
```

- [ ] **Step 6: Commit**

```bash
git add app/services/music_batch/concat.py test/services/music_batch/test_concat.py
git commit -m "feat: add safe batch compilation"
```

---

### Task 7: Batch Manager, Existing-Core Adapter, Retry, Parallelism, Reports

**Files:**
- Create: `app/services/music_batch/manager.py`
- Create: `test/services/music_batch/test_manager.py`
- Modify only if required by adapter seam: `app/services/task.py`

**Interfaces:**
- Consumes all Tasks 1-6 interfaces.
- Produces: `MusicBatchManager` with `create_batch(...)`, `run_batch(...)`, `resume_batch(...)`, `retry_failed(...)`, `approve_reencode(...)`.
- Internal seam: `render_song(song: SongItem, resolved: dict[str, object], output_path: Path) -> Path` constructs the existing `TaskVideoRequest`/`VideoParams` fields, including `custom_audio_file`, and delegates to existing rendering/task services.

- [ ] **Step 1: Write failing test proving existing task parameters are reused**

```python
def test_render_song_builds_existing_video_request(monkeypatch, tmp_path):
    captured = {}
    manager = MusicBatchManager(render_adapter=lambda params, out: captured.update(params=params) or out)
    song = SongItem(source_path=str(tmp_path / "song.mp3"), added_index=0)
    settings = BatchSettings(
        output_root=str(tmp_path),
        video_script="Peaceful nature",
        video_keywords=["ocean", "forest"],
        stock_sources=["pexels"],
        video_encoder="h264_nvenc",
    )
    manager.render_song(song, resolve_song_settings(settings, song), tmp_path / "song.mp4")
    assert captured["params"]["custom_audio_file"].endswith("song.mp3")
    assert captured["params"]["video_script"] == "Peaceful nature"
    assert captured["params"]["subtitle_enabled"] is False
    assert captured["params"]["bgm_type"] == ""
```

- [ ] **Step 2: Write failing retry/continue test**

```python
def test_failed_song_retries_then_continues(tmp_path):
    calls = {"a": 0, "b": 0}
    def renderer(song, *_args):
        name = Path(song.source_path).stem
        calls[name] += 1
        if name == "a":
            raise RuntimeError("render failed")
        return tmp_path / "b.mp4"

    manager = MusicBatchManager(song_renderer=renderer)
    state = manager.run_batch(make_batch(tmp_path, names=["a.mp3", "b.mp3"], retry_count=2))
    assert calls["a"] == 3
    assert calls["b"] == 1
    assert state.songs[0].status == SongStatus.failed
    assert state.songs[1].status == SongStatus.completed
```

- [ ] **Step 3: Run RED**

Run: `uv run pytest test/services/music_batch/test_manager.py -v`

- [ ] **Step 4: Implement sequential manager first (`parallel_jobs=1`)**

Persist state before/after every meaningful transition. Render to an incomplete path, promote with atomic rename only after success, store latest error and attempt count.

- [ ] **Step 5: Add failing parallel-state test then implement bounded executor**

Use `ThreadPoolExecutor(max_workers=settings.parallel_jobs)` only when `parallel_jobs > 1`. All state mutations pass through `BatchStateStore.mutate`; workers never write JSON directly.

- [ ] **Step 6: Add report tests and implement `batch_report.json` + `batch_report.txt`**

Reports include counts, durations, per-song attempts/output/error, global settings snapshot, compilation status, and compilation members.

- [ ] **Step 7: Add compile-decision tests**

If combine is enabled and outputs are compatible, call stream-copy automatically. If incompatible, set a state/result requiring explicit re-encode confirmation and do not call `concat_reencode` until `approve_reencode`.

- [ ] **Step 8: Run manager tests plus existing task tests**

```bash
uv run pytest test/services/music_batch/test_manager.py test/services/test_task.py -v
uv run ruff check app/services/music_batch/manager.py test/services/music_batch/test_manager.py
```

If the repo's exact task test filename differs, use the existing task-service test file under `test/services/` discovered at execution time; do not skip regression coverage.

- [ ] **Step 9: Commit**

```bash
git add app/services/music_batch/manager.py test/services/music_batch/test_manager.py app/services/task.py
git commit -m "feat: add music batch orchestration"
```

Omit `app/services/task.py` if no adapter change was needed.

---

### Task 8: Integration Test Through Existing Core Boundary

**Files:**
- Create: `test/services/music_batch/test_integration.py`

**Interfaces:**
- Exercises the public `MusicBatchManager` with provider/render boundaries mocked at the lowest practical network/FFmpeg seam.

- [ ] **Step 1: Add integration test for two songs with different overrides**

```python
def test_two_song_batch_uses_global_and_override_settings(tmp_path, fake_renderer):
    batch = make_batch(
        tmp_path,
        names=["001.mp3", "002.mp3"],
        video_script="global script",
        keywords=["ocean"],
    )
    batch.songs[1].override = SongOverride(video_keywords=["forest"])
    state = MusicBatchManager(song_renderer=fake_renderer).run_batch(batch)
    assert [s.status for s in state.songs] == [SongStatus.completed, SongStatus.completed]
    assert fake_renderer.calls[0].keywords == ["ocean"]
    assert fake_renderer.calls[1].keywords == ["forest"]
```

- [ ] **Step 2: Add integration test for interrupted resume**

Create durable state with first song completed, second processing, third pending; call resume; verify only second/third are rendered and first is untouched.

- [ ] **Step 3: Add integration test for multi-source plan and best-effort reuse**

Mock provider results; assert selected providers are all exercised and duplicate avoidance filters previously used IDs when alternatives exist.

- [ ] **Step 4: Run integration tests**

Run: `uv run pytest test/services/music_batch/test_integration.py -v`

Expected: PASS with no real network calls.

- [ ] **Step 5: Commit**

```bash
git add test/services/music_batch/test_integration.py
git commit -m "test: add music batch integration coverage"
```

---

### Task 9: Streamlit Music Batch UI Without Disturbing Existing Pages

**Files:**
- Create: `webui/music_batch.py`
- Create: `test/test_music_batch_webui.py`
- Modify: `webui/Main.py` minimally.

**Interfaces:**
- Consumes: `discover_audio_files`, models, `run_preflight`, `MusicBatchManager`, persisted state.
- Produces: `render_music_batch_page()` called only when the new navigation/mode is selected.

- [ ] **Step 1: Write import/surface tests before integration**

```python
def test_music_batch_ui_module_exports_renderer():
    from webui.music_batch import render_music_batch_page
    assert callable(render_music_batch_page)
```

Add a source-level regression assertion that existing primary Video Generator entry logic remains present in `webui/Main.py` after the minimal navigation change.

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/test_music_batch_webui.py -v`

- [ ] **Step 3: Implement isolated page sections**

Required UI sections:

```text
Input
  - Upload Multiple Files
  - Folder Path
  - Include subfolders
Output
  - Output Folder
Global Settings
  - Video Script / Keywords
  - multi-select Pexels/Pixabay/Coverr
  - Aspect / Resolution options mapped to existing system
  - Clip Duration / Concat / Transition / Speed / Encoder
  - Retry count default 2
  - Parallel Jobs default 1, max 4, warning >1
  - Sort mode
  - Avoid reusing clips (OFF)
  - Combine all videos
Song Table
  - Global / Override per song
  - Reset to Global
Execution
  - Preflight results
  - Start Batch
  - Progress/status
  - Resume / Start Over when incomplete batch detected
  - Retry Failed
  - Re-encode and combine / Keep separate when confirmation required
```

- [ ] **Step 4: Integrate into `webui/Main.py` with the smallest possible branch/import**

Do not move existing Video Generator code into new architecture as part of this feature. The integration should only add the new entry and delegate to `render_music_batch_page()`.

- [ ] **Step 5: Run UI/import tests and existing main tests**

```bash
uv run pytest test/test_music_batch_webui.py test/test_main.py -v
uv run ruff check webui/music_batch.py webui/Main.py test/test_music_batch_webui.py
```

- [ ] **Step 6: Commit**

```bash
git add webui/music_batch.py webui/Main.py test/test_music_batch_webui.py
git commit -m "feat: add music batch web ui"
```

---

### Task 10: Documentation, Full Regression Gates, and Local GPU Smoke-Test Instructions

**Files:**
- Modify: `README-en.md`
- Modify: `README.md`
- Verify: all Music Batch and existing test files.

**Interfaces:**
- No new runtime interface; this task closes the feature and documents the real-machine validation path.

- [ ] **Step 1: Document additive feature and usage**

Document that Music Batch is additional to the existing generator, supported audio formats, folder/upload input, overrides, resume, retry, multi-source selection, NVENC visibility, output layout, and optional compilation.

- [ ] **Step 2: Document local Windows smoke test**

Use three approximately 3-minute songs and run:

```text
Song 1: Pexels
Song 2: Pixabay
Song 3: Pexels + Pixabay + Coverr
Encoder: h264_nvenc
Parallel Jobs: 1
Combine All: enabled
```

Verify: GPU Video Encode activity, each output duration matches its source audio within normal container rounding, audio is present, batch resume works, a forced retry works, and compilation is produced or explicitly requests re-encode approval.

- [ ] **Step 3: Run full automated quality gates**

```bash
uv sync --frozen
uv run ruff check .
uv run pytest
uv run coverage run -m pytest
uv run coverage report
```

Expected: all tests PASS; coverage remains >= 70%; no existing test is removed/disabled to achieve this.

- [ ] **Step 4: Run regression diff review**

Run:

```bash
git diff main...HEAD --stat
git diff main...HEAD -- webui/Main.py app/services/task.py app/services/material.py
```

Review criteria: existing logic was not deleted or semantically replaced; shared-core changes, if any, are narrow and backward-compatible; no secrets/API keys/config.toml were added.

- [ ] **Step 5: Commit documentation**

```bash
git add README-en.md README.md
git commit -m "docs: document music batch workflow"
```

- [ ] **Step 6: Create PR only after the branch passes automated gates**

PR title: `feat: add Music Batch mode`

PR body must summarize: additive architecture, existing-feature preservation, new test coverage, known local GPU validation still required, and exact automated gate results.

---

## Final Verification Checklist

- [ ] Multiple file upload works.
- [ ] Folder input defaults to non-recursive.
- [ ] Include subfolders works.
- [ ] Output batch subdirectory is created safely.
- [ ] Global Script/Keywords work without forced LLM generation.
- [ ] Per-song overrides work and Reset to Global removes them.
- [ ] Pexels/Pixabay/Coverr can be selected individually or together.
- [ ] Multi-source requests are distributed rather than first-provider-only.
- [ ] Avoid clip reuse is OFF by default and best-effort when ON.
- [ ] Default is 1920x1080 / 16:9; existing aspect options remain available.
- [ ] Retry default is 2 retries after initial attempt.
- [ ] A song failure does not stop unrelated songs.
- [ ] Resume skips completed songs and recovers interrupted `processing` songs.
- [ ] Start Over preserves old run data.
- [ ] Retry Failed resets only failed songs.
- [ ] Parallel Jobs defaults to 1, max 4, and warns above 1.
- [ ] State updates are concurrency-safe and atomically persisted.
- [ ] Output naming never silently overwrites files.
- [ ] Incomplete render files are never treated as completed.
- [ ] NVENC preflight failure is visible and not silently converted to an unnoticed CPU batch.
- [ ] Compatible final files stream-copy concatenate.
- [ ] Incompatible files require approval before full re-encode.
- [ ] Failed/skipped songs are omitted from compilation and reported.
- [ ] `batch_report.json` and `batch_report.txt` are generated.
- [ ] Existing Video Generator/API/CLI/TTS/subtitle/BGM/provider/encoder/config behavior remains intact.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run pytest` passes.
- [ ] Coverage remains >= 70%.
- [ ] No secrets or local `config.toml` are committed.
