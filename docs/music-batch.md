# Music Batch

Music Batch is an additive VideosTurbo workflow for creating one stock-footage video per audio track. It reuses the existing MoneyPrinterTurbo/VideosTurbo rendering services and does not replace the normal Video Generator, API, CLI, TTS, subtitle, background-music, provider, or encoder workflows.

## Open Music Batch

Start the normal WebUI, for example on Windows:

```powershell
.\webui.bat
```

Open the **Music Batch** page from the Streamlit page navigation.

## Input

Two input methods are supported and can be used together:

- **Upload Multiple Files** — select multiple `.mp3`, `.wav`, `.m4a`, or `.flac` files.
- **Folder Path** — enter a local folder containing audio files.

Folder scanning is non-recursive by default. Enable **Include subfolders** to scan nested directories.

## Output

Choose an **Output Folder** for each batch. Music Batch creates a new batch subdirectory and keeps state/report files there.

Typical output:

```text
batch_2026-08-20_001/
├─ 001.mp4
├─ 002.mp4
├─ Full_Compilation.mp4       # only when requested and completed
├─ batch_state.json
├─ batch_report.json
└─ batch_report.txt
```

Video filenames are based on the source audio filenames. Existing outputs are never silently overwritten; numeric suffixes are added when needed.

## Global Settings and Song Overrides

The page provides global settings for the entire batch, including:

- Video Script
- Video Keywords
- Stock Video Sources
- Aspect Ratio / Resolution
- Clip Duration
- Concat Mode
- Transition
- Clip Speed
- Video Encoder
- Retry Count
- Parallel Jobs
- Sort Order
- Avoid reusing clips in this batch
- Combine all videos after batch

Each song can optionally enable **Override global settings** for script, keywords, stock sources, clip duration, concat mode, transition, and clip speed. Use **Reset to Global** to remove song-specific values.

## Stock Sources

Music Batch supports Pexels, Pixabay, and Coverr. One or multiple providers can be selected. When multiple providers are selected, the required footage duration is distributed across them instead of exhausting only the first provider.

Provider API keys must already be configured in the normal VideosTurbo settings.

## Avoid Reusing Clips

**Avoid reusing clips in this batch** is OFF by default.

When enabled, Music Batch tracks stable downloaded-material identifiers and attempts to avoid footage already used earlier in the same batch. This is best-effort: if all available search results have already been used, footage may be reused instead of failing the song solely because the search pool is exhausted.

## Retry and Failure Handling

Retry Count defaults to `2`, meaning one initial attempt plus up to two retries.

A normal song-level error retries that song and, if retries are exhausted, marks it failed and continues with the next song.

Batch-level failures such as unsafe state persistence or a detected hardware-encoder fallback stop the batch because continuing could make a long run misleading or unrecoverable.

## NVENC and Other Encoders

Music Batch uses the existing VideosTurbo encoder mechanism. Before the batch starts, an FFmpeg encoder probe is performed.

For `h264_nvenc`, a successful preflight means FFmpeg can encode a short probe with the selected NVIDIA encoder. During the batch, if the existing renderer disables the requested hardware codec after a runtime failure and falls back to `libx264`, Music Batch stops and surfaces the failure rather than silently continuing a long CPU render.

## Parallel Jobs

Parallel Jobs defaults to `1` and can be set up to `4`.

Values above `1` may substantially increase CPU, RAM, GPU, VRAM, disk, and network usage. For modest local hardware, start with `1`.

## Resume and Start Over

Music Batch persists state atomically in `batch_state.json`.

If a batch is interrupted:

- **Resume** keeps completed songs and continues pending work.
- Songs that were `processing` or `retrying` when interrupted return to a retryable pending state.
- **Start Over** creates a new restart directory and leaves the previous run untouched.
- **Retry Failed** gives failed songs a fresh retry budget without re-rendering completed songs.

## Final Compilation

Enable **Combine all videos after batch** to request one final compilation.

Music Batch first probes completed files with `ffprobe` and checks relevant video/audio stream properties. If compatible, it uses FFmpeg concat with stream copy (`-c copy`) so the final join is fast and does not re-encode the full compilation.

If the files are incompatible, Music Batch does not silently start a potentially long re-encode. It waits for an explicit decision:

- **Re-encode and Combine**
- **Keep Separate Videos**

Only completed songs are included in the final compilation, in the selected Filename Order or Added Order.

## Reports

Each batch writes:

- `batch_state.json` — durable resume state.
- `batch_report.json` — machine-readable summary with per-song status and errors.
- `batch_report.txt` — human-readable summary.

## Recommended Local Workflow

For a long relaxation-music batch on a Windows PC with a working NVIDIA NVENC setup:

1. Start with `Parallel Jobs = 1`.
2. Select `h264_nvenc`.
3. Use a non-empty Video Script and manually supplied English Video Keywords to avoid unnecessary LLM calls.
4. Select one or more of Pexels, Pixabay, and Coverr.
5. Leave clip reuse allowed initially; enable duplicate avoidance only when desired.
6. Enable final compilation only when you want the individual videos joined automatically.
7. Verify the first small batch before launching dozens of songs.

## Verification Before Long Runs

Before trusting a multi-hour batch, test with a few short songs and confirm:

- the Music Batch page loads without affecting the normal Video Generator;
- the selected stock providers work;
- output duration/audio are correct;
- the selected hardware encoder is active;
- Resume works after an interrupted test;
- Retry Failed works;
- final stream-copy compilation works when outputs are compatible.
