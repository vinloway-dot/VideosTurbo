# Music Batch Design

Date: 2026-08-20
Repository: `vinloway-dot/VideosTurbo`
Branch: `feature/music-batch`
Status: Approved design specification

## 1. Goal

Add a new **Music Batch** feature to VideosTurbo/MoneyPrinterTurbo without removing, replacing, or reducing any existing functionality.

The feature is intended for workflows such as relaxation-music videos where the user has many audio tracks and wants to automatically create one stock-footage video per song, optionally combine all completed videos into one long compilation, and resume interrupted batches safely.

The existing Video Generator, API, CLI, TTS, subtitles, background music, stock-source integrations, rendering, encoding, configuration, and all other existing features must continue to work as before.

This is an additive feature. Existing behavior is a regression-protected requirement.

## 2. Core Design Principle

Music Batch will be implemented as an orchestration layer above the existing MoneyPrinterTurbo rendering/task services.

It must reuse the existing core for:

- stock-video retrieval
- resize/crop/compositing
- clip concatenation
- transitions
- audio handling
- video rendering
- FFmpeg processing
- encoder selection, including `h264_nvenc`

The batch feature must not create a second independent video-rendering engine.

The UI should be added mostly in new files. Existing large files such as `webui/Main.py` should be changed only as much as needed to expose the new feature.

## 3. Existing Features Must Remain Intact

The following are explicitly out of scope for removal or replacement:

- existing Video Generator workflow
- existing AI script generation
- existing TTS/voice functions
- existing subtitle functions
- existing background music functions
- existing Pexels/Pixabay/Coverr behavior in the normal Video Generator
- existing API endpoints
- existing CLI behavior
- existing encoder choices
- existing configuration behavior

Music Batch may call shared services used by these features, but must not alter existing behavior unless a narrowly scoped compatibility change is required. Any such change must be covered by regression tests.

## 4. User Entry Point

Add a new **Music Batch** section/page in the WebUI while preserving the current pages and workflows.

Conceptually:

- Video Generator — existing
- Music Batch — new
- Video Tasks — existing
- Settings — existing

The precise navigation implementation may follow the current Streamlit structure, but the new feature must appear as a clearly separate mode.

## 5. Audio Input

Music Batch supports two input methods.

### 5.1 Multiple-file upload

The user can select multiple audio files at once through the WebUI.

Supported extensions:

- `.mp3`
- `.wav`
- `.m4a`
- `.flac`

Existing custom-audio support may allow more formats internally, but these four are the required batch formats.

### 5.2 Folder input

The user can provide a local folder path, for example:

`D:\Music\Album01`

Default behavior: read audio files only from the selected folder itself.

Optional checkbox:

`Include subfolders`

When disabled, subdirectories are ignored. When enabled, supported audio files are discovered recursively.

### 5.3 Deduplication and validation

The batch input layer must avoid accidentally adding the same physical file twice when possible. Invalid or unreadable files must be surfaced before or during batch execution and must not crash the whole batch.

## 6. Output Folder and Batch Directory

The user selects an output folder for each batch.

The system creates a new subdirectory automatically, for example:

`D:\MyVideos\batch_2026-08-20_001\`

Typical contents:

- one `.mp4` per completed song
- `Full_Compilation.mp4` when requested and successfully created
- `batch_state.json`
- `batch_report.json`
- `batch_report.txt`

A restarted batch must not silently destroy the previous batch directory. A Start Over action should create a new run directory or an equivalent safe new run identifier.

## 7. Output Naming

The default output name is derived from the source audio filename.

Example:

`01 - Calm Ocean.mp3` -> `01 - Calm Ocean.mp4`

Existing files must not be overwritten silently.

If a target filename already exists, append a numeric suffix:

- `01 - Calm Ocean.mp4`
- `01 - Calm Ocean_2.mp4`
- `01 - Calm Ocean_3.mp4`

Rendering should preferably target a temporary/incomplete filename first, such as `.rendering.mp4`, and only promote/rename it to the final filename after successful completion.

An incomplete temporary file must not count as a completed song on Resume.

## 8. Global Settings and Per-Song Override

Music Batch provides global settings used by every song unless that song has an override.

Required global settings include:

- Video Script
- Video Keywords
- stock sources
- aspect ratio / resolution
- clip duration
- concat mode
- transition mode
- clip speed
- video encoder
- retry count
- parallel jobs
- sort mode
- avoid clip reuse option
- combine-all option

Each song may enable **Override** and replace applicable global values for that song.

Required per-song override support:

- Video Script
- Video Keywords
- stock sources
- clip duration
- concat mode
- transition mode
- clip speed

A song without an override always resolves to the current batch-global values.

A `Reset to Global` action should remove song-specific override values.

## 9. Script and Keywords

The normal use case must support manually supplied non-empty Video Script and Video Keywords so that the batch can avoid unnecessary LLM generation.

Music Batch should translate these settings into the existing task model rather than introducing a separate script/keyword pipeline.

## 10. Stock Video Sources

Music Batch must support:

- Pexels only
- Pixabay only
- Coverr only
- any multi-source combination, including Pexels + Pixabay + Coverr

The user may set global sources and optionally override them per song.

### 10.1 Multi-source behavior

When multiple sources are selected, Music Batch should distribute stock requests/results across the selected providers rather than exhausting the first provider before trying others.

Existing single-source behavior outside Music Batch must remain unchanged.

The implementation should reuse existing provider-specific download/search services where possible.

## 11. Avoid Reusing Clips

Add checkbox:

`Avoid reusing clips in this batch`

Default: **OFF**.

When OFF, MoneyPrinterTurbo behaves normally and clip reuse is allowed.

When ON, Music Batch tracks provider clip IDs or stable clip identifiers that were already used in this batch and attempts to avoid selecting them again.

This is a **best-effort** rule, not an absolute guarantee. If the available search pool is too small, the system may reuse a clip rather than fail the batch solely because no unseen clip is available.

## 12. Resolution and Aspect Ratio

Default for Music Batch:

- 1920x1080
- 16:9

The user must still be able to choose other aspect-ratio/resolution options already supported by the existing system.

Music Batch must not remove any existing encoder or aspect-ratio capability from the normal application.

## 13. Encoder

The batch feature uses the normal encoder selection supported by VideosTurbo.

For the target local workflow, `h264_nvenc` is expected to be selectable.

Music Batch should run a preflight or equivalent validation for the selected encoder where practical.

If `h264_nvenc` is unavailable at batch start or fails in a way that would cause a CPU fallback, Music Batch must make that fallback visible to the user. For long batches, it must not silently turn a GPU batch into a CPU batch without user awareness.

The normal Video Generator's legacy fallback behavior should not be changed unless required by shared-core correctness.

## 14. Sorting

Support two batch order modes:

1. Filename Order
2. Added Order

The selected order applies both to:

- song processing order
- final compilation order

Filename ordering should use a user-friendly/natural ordering where feasible so names such as `2.mp3` and `10.mp3` sort intuitively.

## 15. Parallel Jobs

Music Batch supports configurable parallel processing.

Default: `1`

Maximum exposed in the UI: `4`

If the user chooses more than 1, display a warning that multiple simultaneous renders can significantly increase CPU, RAM, GPU, VRAM, disk, and network usage.

State management must remain correct under parallel processing. Workers must not corrupt `batch_state.json` through concurrent writes.

The state layer should serialize state mutations or otherwise guarantee atomic/safe updates.

## 16. Retry Behavior

Retry count is configurable in the UI.

Default: `2` retries.

Interpretation:

- first normal attempt
- then up to N retries after failure

For a failed song:

1. record the error
2. retry until configured retries are exhausted
3. if still failing, mark the song `failed`
4. continue to the next song

A single-song failure must not terminate the whole batch unless it exposes a batch-level fatal error.

## 17. Batch and Song State

Required song states:

- `pending`
- `processing`
- `retrying`
- `completed`
- `failed`
- `skipped`

Required batch-level states may include:

- `pending`
- `processing`
- `completed`
- `completed_with_failures`
- `failed`
- `interrupted`

The exact model can be refined during implementation, but externally visible semantics must match these requirements.

## 18. State Persistence

Each batch stores durable state in its batch output directory, including at minimum `batch_state.json`.

State must be persisted after meaningful transitions, especially:

- batch creation
- song enters processing
- retry count changes
- song completes
- song fails
- compilation starts/ends
- batch completes

Writes should be safe against partial-file corruption. Prefer atomic write-then-replace semantics rather than writing JSON directly into the final state file in place.

A state write failure is a batch-level fatal condition because Resume can no longer be trusted.

## 19. Resume and Start Over

When the WebUI detects an incomplete batch, present:

- Resume
- Start Over

### 19.1 Resume

On resume:

- `completed` songs are not re-rendered
- previous `processing` songs are treated as interrupted and returned to `pending` (or an equivalent retryable state)
- `pending` songs continue normally
- `failed` songs remain failed by default
- the UI should provide a way to retry failed songs without restarting the whole batch

Final output existence may be used as an additional integrity check, but the system must not infer completion from file existence alone.

### 19.2 Start Over

Start Over creates a new run rather than silently deleting the previous run.

Example:

- `batch_2026-08-20_001`
- `batch_2026-08-20_001_restart_01`

Equivalent safe naming is acceptable.

## 20. Fatal vs Song-Level Errors

### 20.1 Song-level errors

Examples:

- provider request failed
- clip download failed
- no usable clips found
- input audio invalid
- FFmpeg failed for this song
- render failed for this song

These trigger retry and then, if exhausted, mark the song failed and continue.

### 20.2 Batch-level fatal errors

Examples:

- output directory cannot be written
- state cannot be persisted safely
- essential executable such as FFmpeg is unavailable
- required internal service cannot initialize

These may stop the batch because continuing would risk corrupted or untrackable output.

## 21. Preflight Validation

Before starting a batch, validate where practical:

- at least one supported input song exists
- selected input files are readable
- output folder exists or can be created
- output folder is writable
- FFmpeg is available
- selected encoder exists/works enough for the planned run
- required API keys exist for selected online stock providers
- free disk space is not critically low

Disk-space checking should provide a warning rather than rely on a fragile exact prediction.

## 22. Combine All Videos

Add checkbox:

`Combine all videos after batch`

Default may follow the UI implementation, but it must be explicit and user-controlled.

When enabled, compilation begins after individual song processing is finished.

Only completed songs are included.

Failed/skipped songs are omitted and reported.

Compilation order follows the selected batch sort mode.

## 23. Fast Compilation Path

Before combining, inspect completed outputs for compatibility, including relevant values such as:

- video codec
- resolution
- frame rate / time base where necessary
- audio codec
- audio sample rate
- audio channel layout

If compatible, use FFmpeg concat with stream copy/no re-encode.

This is the preferred path for long compilations.

## 24. Incompatible Compilation Path

If files cannot safely be stream-copied into one output, do **not** silently launch a multi-hour re-encode.

Return a visible state such as:

`Cannot stream-copy compilation: incompatible output formats`

Then offer the user a choice:

- Re-encode and combine
- Keep separate videos only

The UI may present this during or after the batch as technically appropriate, but user consent is required before a potentially expensive full re-encode.

## 25. Batch Reports

Create both machine-readable and human-readable reports.

### 25.1 `batch_report.json`

Include at minimum:

- batch ID
- start/end time
- total processing time
- global settings snapshot
- selected sort order
- total songs
- completed count
- failed count
- skipped count
- per-song source path/name
- per-song final status
- attempts/retries
- output path when successful
- latest error when failed
- compilation status/path
- songs included in compilation

### 25.2 `batch_report.txt`

Provide a concise human-readable summary, including failures and reasons.

## 26. Proposed Code Structure

Prefer new modules over expanding `webui/Main.py` further.

Target structure:

```text
app/
  services/
    music_batch/
      __init__.py
      manager.py
      models.py
      state.py
      sources.py
      concat.py

webui/
  Main.py                 # minimal integration change
  music_batch.py          # new Music Batch UI
```

Additional small utility/test files may be introduced as needed.

Responsibilities:

### `manager.py`

- create/start/resume batch
- resolve global + override settings
- schedule songs
- enforce retry policy
- coordinate parallel jobs
- call existing rendering/task services
- update state/report layer

### `models.py`

- batch configuration model
- song item model
- override model
- enums/status values
- serialization-friendly structures

### `state.py`

- durable state persistence
- atomic JSON writes
- load/recover incomplete batches
- concurrency-safe state mutations

### `sources.py`

- Music Batch multi-source orchestration
- distribute provider requests
- used-clip tracking for optional duplicate avoidance
- reuse existing provider implementations

### `concat.py`

- probe output compatibility
- stream-copy concat
- explicit re-encode path only after user approval

### `webui/music_batch.py`

- batch input UI
- global settings
- song list and overrides
- progress/status display
- resume/start-over controls
- retry-failed controls
- combine decision workflow

## 27. Data Flow

```text
Audio inputs
  -> scan/validate
  -> batch configuration
  -> resolve sort order
  -> create durable batch state
  -> for each song
       -> resolve global + per-song overrides
       -> resolve stock-source strategy
       -> construct existing task/video parameters
       -> call existing core renderer/task services
       -> capture output
       -> persist song state/report
  -> optional compilation compatibility check
  -> stream-copy combine OR ask before re-encode
  -> finalize reports and batch state
```

## 28. Testing Strategy

The repository already uses pytest, coverage, and ruff. Music Batch must integrate with that quality system.

### 28.1 Unit tests

Cover at minimum:

- folder scanning
- Include subfolders behavior
- multiple-file normalization
- supported-extension filtering
- filename and added-order sorting
- global setting resolution
- per-song override resolution
- Reset to Global semantics
- output filename collision handling
- retry counter behavior
- state transitions
- interrupted-processing recovery
- atomic state persistence
- retry-failed behavior
- multi-source distribution
- used-clip tracking and best-effort reuse fallback
- report generation
- concat compatibility decisions

### 28.2 Integration tests

Exercise the path:

`audio input -> batch manager -> existing task/service integration -> output handling`

Network provider calls should normally be mocked/stubbed in automated tests so CI does not depend on live Pexels/Pixabay/Coverr availability or credentials.

### 28.3 Regression tests

Existing tests must continue to pass.

Do not weaken or remove tests to accommodate Music Batch.

If a shared-core modification is needed, add regression coverage demonstrating that the original single-video workflow still behaves correctly.

### 28.4 Coverage

Do not lower the repository's existing coverage threshold to make the feature pass.

## 29. Local Smoke Test

After code is implemented and pulled to the target Windows machine, run a real local smoke test because GitHub CI cannot reproduce the user's local GTX 1060 + NVIDIA driver + local provider credentials.

Minimum recommended smoke batch:

- 3 songs, roughly 3 minutes each
- Song 1: Pexels
- Song 2: Pixabay
- Song 3: Pexels + Pixabay + Coverr

Verify:

- `h264_nvenc` is actually used
- output duration matches audio closely
- audio is correct
- resume works after an intentional interruption
- retry behavior works
- final combine path works
- existing single-video generation still works

## 30. Branch and Pull Request Strategy

Development occurs on:

`feature/music-batch`

Do not implement directly on `main`.

Expected flow:

1. keep `main` as known-good baseline
2. implement and test on `feature/music-batch`
3. review diff for accidental deletions or unrelated refactors
4. run quality gates
5. open Pull Request to `main`
6. merge only after review and verification

## 31. Quality Gates Before Merge

At minimum:

- dependency environment resolves with the repository's uv workflow
- Ruff passes
- pytest passes
- coverage remains at or above the repository threshold
- no secrets/API keys committed
- `config.toml` with local secrets is not committed
- existing feature tests remain green
- Music Batch tests pass
- diff review confirms no unnecessary feature deletion

## 32. Upstream Compatibility

`vinloway-dot/VideosTurbo` is intended to remain maintainable as a fork of upstream MoneyPrinterTurbo.

To reduce future merge conflicts:

- prefer new modules
- keep changes to `webui/Main.py` minimal
- avoid unrelated refactors
- reuse public/internal service boundaries that already exist
- document any shared-core changes carefully

Future upstream sync should conceptually remain:

`harry0703/MoneyPrinterTurbo -> VideosTurbo main -> preserve Music Batch modules`

## 33. Acceptance Criteria

The feature is complete only when all of the following are true:

1. Existing VideosTurbo/MoneyPrinterTurbo functionality remains available.
2. Music Batch can accept multiple uploaded audio files.
3. Music Batch can scan a folder with optional subfolder recursion.
4. User can select an output folder and the system creates an isolated batch directory.
5. Default output is 1920x1080 16:9, while existing supported aspect/resolution options remain selectable.
6. Global settings can be overridden per song.
7. Single and multiple stock sources are supported.
8. Avoid-reuse option exists and defaults OFF.
9. Retry count is configurable and defaults to 2.
10. Parallel jobs are configurable, default to 1, and UI warns above 1.
11. Sort supports Filename Order and Added Order.
12. Failed songs do not automatically abort the whole batch.
13. Durable state supports Resume after interruption.
14. Incomplete `processing` items recover safely on Resume.
15. Start Over does not silently destroy the previous run.
16. Output filename collisions do not overwrite existing files.
17. Optional Combine All works for compatible outputs with stream copy.
18. Expensive re-encode compilation requires explicit user approval when stream copy is not possible.
19. JSON and text reports are generated.
20. Automated unit/integration/regression tests pass.
21. Existing coverage threshold is not lowered.
22. A real local smoke test confirms NVENC and the end-to-end music-batch workflow.

## 34. Non-Goals for This Phase

To keep scope controlled, this phase does not require:

- cloud-distributed rendering
- a database server solely for batch state
- cross-machine worker orchestration
- automatic YouTube/social publishing
- automatic song generation
- automatic AI keyword generation as a requirement
- a global permanent stock-video library/cache beyond what existing services already do

These can be considered later without blocking the initial Music Batch feature.
