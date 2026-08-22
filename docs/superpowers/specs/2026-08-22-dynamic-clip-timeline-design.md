# Dynamic Clip Timeline Design

## Status and relationship to the existing Six-Clip design

This design supersedes only the fixed-count and fixed-60-second behavior defined by `2026-08-22-six-clip-media-timeline-design.md`. The original document remains unchanged as the historical baseline.

Implementation remains on `feature/six-clip-media-timeline` and continues through Draft PR #3. Do not create a duplicate branch or pull request. Do not merge until the automated and Windows validation gates pass.

## Goal

Let the Main Generator keep the existing six-slot experience for narration up to 60 seconds, while automatically expanding the number of user-supplied visual slots when confirmed narration is longer. Narration duration becomes the source of truth for the final visual timeline, following the working duration-driven behavior already present in MoneyPrinterTurbo.

## Approved user flow

1. **Generate Script** creates or updates text only. It must not call TTS.
2. The user edits the script and selects the voice provider, voice, rate, and volume.
3. The user explicitly clicks **Confirm Script & Build Timeline**. This action may call a paid TTS provider and must be labeled accordingly.
4. The application generates one full narration preview, measures its exact duration, computes the required clip count, and builds the timeline plan.
5. The same confirmed narration audio and subtitle timing object are reused by the final video task. Final generation must not silently call TTS again while the confirmed cache is valid.
6. The UI renders the computed media slots. The user imports a direct media URL or uploads an image/video for each slot.
7. Final rendering is enabled only when the narration confirmation is current and every required slot has valid media.
8. If the script, provider, voice, rate, volume, or relevant provider configuration changes, the plan becomes stale. Final rendering is blocked until the user confirms again.

## Timeline calculation

Use the measured narration duration as a finite positive floating-point number `D`.

```text
slot_duration_seconds = 10.0
minimum_clip_count = 6
clip_count = max(minimum_clip_count, ceil(D / slot_duration_seconds))
timeline_duration_seconds = max(60.0, D)
```

Rules:

- Narration at or below 60 seconds keeps six visual slots: 0–10, 10–20, 20–30, 30–40, 40–50, and 50–60.
- Narration above 60 seconds creates as many slots as required.
- Every non-final slot spans exactly 10 seconds.
- The final slot ends at `timeline_duration_seconds`. It may be shorter than 10 seconds.
- Source media for every slot may still be a 10-second AI-generated clip. The renderer uses only the required portion of the final source clip.
- The final rendered visual duration is `timeline_duration_seconds`.
- Automatic voice-speed adjustment is not part of the default flow. It may be designed later as an explicit “force to 60 seconds” option.
- Repeating the original six clips is not the normal path. It may be added later only as an explicit fallback selected by the user.

Examples:

| Narration | Slots | Final range | Final video |
|---:|---:|---:|---:|
| 55.0 s | 6 | 50–60 s | 60.0 s |
| 60.0 s | 6 | 50–60 s | 60.0 s |
| 63.0 s | 7 | 60–63 s | 63.0 s |
| 88.0 s | 9 | 80–88 s | 88.0 s |
| 127.0 s | 13 | 120–127 s | 127.0 s |

## Duration source and narration reuse

The planning duration must come from the generated audio file or the existing full-preview result, not from word-count estimation and not from the current rounded integer returned by `generate_audio`.

The existing local duration estimate remains an informational pre-confirmation hint. It may show an estimated duration range and estimated slot count, but it cannot create the authoritative timeline.

The confirmed narration cache is identified by the existing full-preview fingerprint inputs:

- complete script text;
- TTS provider;
- voice name;
- voice rate;
- voice volume;
- non-secret provider configuration signature.

The timeline plan stores the resulting narration fingerprint and measured duration. Secrets and raw API keys must never be stored in the plan or task history.

If the Streamlit session loses the reusable subtitle timing object, the application must mark the confirmation stale and ask the user to confirm again. It must not trigger a paid TTS request without a user action.

## Data model and compatibility strategy

Keep the current `SixClipPlan`, `SixClipSegment`, `six_clip_mode`, and `six_clip_plan` public names during this change. They are already persisted in task history and imported by the Cloud Agent branch. Renaming is a separate cleanup after end-to-end acceptance.

Generalize their behavior:

### `SixClipSegment`

- `index: int`, starting at 1 with no fixed maximum of 6;
- `start_sec: float`;
- `end_sec: float`, strictly greater than `start_sec`;
- existing title, narration context, video prompt, media kind, and media path fields;
- validation against the range calculated for its position in the plan.

### `SixClipPlan`

Add:

- `narration_duration_sec: float = 60.0`;
- `timeline_duration_sec: float = 60.0`;
- `slot_duration_sec: float = 10.0`;
- `narration_fingerprint: str = ""`;
- ordered segments with a minimum length of six and no hard-coded six-item maximum.

Old six-segment JSON without the new fields loads with the defaults above and retains its existing 60-second behavior.

Add pure helpers with stable interfaces:

```python
def build_timeline_ranges(
    narration_duration_sec: float,
    *,
    slot_duration_sec: float = 10.0,
    minimum_clip_count: int = 6,
    maximum_clip_count: int = 0,
) -> tuple[tuple[float, float], ...]: ...

def validate_timeline_plan(plan: SixClipPlan) -> SixClipPlan: ...

def is_timeline_current(
    plan: SixClipPlan,
    narration_fingerprint: str,
) -> bool: ...
```

`maximum_clip_count=0` means no product-level cap. Administrators may set `app.max_dynamic_clip_count` to a positive value as an operational safety limit. If configured, confirmation fails before clip-plan LLM generation when the computed count exceeds that value.

## Script analysis and prompt generation

Replace the fixed “exactly six clips” contract with a range-driven contract.

Inputs:

- complete confirmed script;
- narration language;
- exact timeline ranges;
- target words for history/display only;
- optional timestamped subtitle cues when the TTS provider supplies them.

Behavior:

1. When usable subtitle timing cues exist, derive ordered narration chunks from the cues before requesting visual prompts.
2. When only full-audio duration is available, ask the existing LLM service to partition the narration chronologically across the exact ranges.
3. Require exactly one returned object per supplied range.
4. Reject missing, extra, reordered, overlapping, or contradictory segments.
5. Preserve the existing global character rules.
6. Every full slot prompt targets a 10-second source video.
7. A partial final slot prompt states that the required action must occur within the first required seconds because the renderer trims the remainder.
8. The master prompt header states the actual number of clips rather than “six”.

For long timelines, present master prompts in batches of six clips. Each batch retains global rules and identifies its absolute clip indexes and time ranges. This avoids a single unbounded copy block and aligns with future Flow batching.

## Streamlit UI and state

The Main Generator keeps three conceptual sections:

### Section 1 — Script and narration confirmation

- **Generate Script** performs LLM text generation only.
- The script remains editable.
- Voice controls remain editable.
- **Confirm Script & Build Timeline** generates/reuses the full narration preview and builds the authoritative plan.
- Display measured narration duration, required clip count, and confirmation status.
- A pre-confirmation estimate is visibly labeled as an estimate.

### Section 2 — Dynamic timeline media

- Rename the visible heading from “Six Video Clips” to “Timeline Clips”.
- Render the plan’s actual segment count.
- Show six clip cards per UI page.
- Stable widget keys continue to use the absolute clip index.
- Imported media already attached to a still-valid segment is preserved across ordinary Streamlit reruns.
- A rebuilt plan preserves media only for segments whose absolute time range is unchanged. Media from removed or changed ranges is not silently reassigned.
- Missing media blocks final generation.

### Section 3 — Master prompts

- Build prompt batches dynamically, six clips per batch.
- Each batch is independently copyable.
- Prompts always reflect current editable narration context and video-prompt values.

## Staleness and edits after confirmation

A plan becomes stale when any narration fingerprint input changes.

When stale:

- show a clear warning;
- retain the visible plan and imported media for review;
- disable final generation;
- offer **Rebuild Timeline**;
- do not call TTS until the user presses the confirmation/rebuild action.

After rebuilding:

- recompute duration and ranges;
- regenerate narration contexts and video prompts;
- retain media only for unchanged ranges;
- keep unmatched media files on disk until normal session/task cleanup, but do not attach them to a different range automatically.

Changing clip titles, narration contexts, video prompts, or media does not invalidate the confirmed audio.

## Media handling

Generalize the existing six-clip media service:

- accept any positive clip index allowed by the current plan;
- keep streamed download, media magic validation, size limits, signed-query redaction, and task-local materialization;
- use zero-padded stable filenames such as `clip-001.mp4`;
- iterate over the plan rather than a fixed range;
- report every missing absolute clip index and time range;
- never fall back to stock providers in timeline mode.

## Rendering

Reuse the working duration-driven principles from MoneyPrinterTurbo and the existing FFmpeg helpers.

- Normalize each supplied video or image into a source clip that covers its slot.
- Preserve plan order.
- Remove source audio.
- Loop source videos shorter than their required slot.
- Trim source videos longer than their required slot.
- Concatenate all prepared sources with `video.concat_video_clips_with_ffmpeg`.
- Pass `plan.timeline_duration_sec` as the final maximum duration rather than the constant 60.
- Require enough user-supplied segments to cover the timeline; do not silently cycle back to Clip 1.
- Reuse existing subtitle, narration, BGM, codec fallback, task-state, and final muxing paths.
- Verify the final file duration is within 0.5 seconds of `plan.timeline_duration_sec`.

## Task pipeline behavior

WebUI final submission supplies the confirmed full-preview payload already supported by the task pipeline.

Preflight order:

1. validate that the plan exists;
2. validate that its narration fingerprint matches the current script and voice settings;
3. validate that all required media are present;
4. validate the reusable narration cache;
5. only then submit final rendering.

Backend/API callers without a reusable preview may still synthesize narration once. After synthesis, the backend computes the expected ranges and compares them to the submitted plan. A mismatch fails with an actionable “confirm/rebuild timeline” error before subtitle, material preparation, or final rendering. It must not truncate speech or silently change speed.

Remove the current `audio_duration > 60` rejection. Replace it with validation that measured narration duration and plan duration/ranges agree.

## History and restoration

Persist:

- exact measured narration duration;
- timeline duration;
- slot duration;
- narration fingerprint;
- all dynamic segments;
- local media references only;
- existing script, voice, subtitle, and rendering parameters.

Restoration:

- old six-segment tasks continue to load;
- dynamic tasks restore all prompts and existing local media;
- missing files restore as Missing;
- narration confirmation restores as stale when the reusable audio/timing cache is unavailable;
- no signed source URL query is restored or logged.

## Cloud Agent boundary

This design changes the shared `SixClipPlan` semantics, but this implementation scope is the SixClip/Main Generator baseline only.

Before Cloud Agent work proceeds into Flow generation or Canva composition:

1. merge or rebase the completed baseline into `feature/cloud-video-agent`;
2. update the Cloud Agent workflow’s “exactly six clips” validation;
3. update Flow generation/download and Canva upload loops to consume the dynamic plan;
4. run all Cloud Agent model/store/worker/workflow regressions;
5. update the Cloud Agent Design Spec and Implementation Plan in place rather than creating duplicate planning sets.

Draft PR #4 remains separate and must not be merged merely because this baseline plan is complete.

## Error handling

- Empty script: confirmation is blocked.
- TTS failure: keep the editable script and report the provider error; create no authoritative plan.
- Invalid/non-finite/zero duration: reject confirmation.
- Configured clip-count limit exceeded: report required count and configured maximum before LLM plan generation.
- Invalid LLM JSON or wrong clip count/ranges: reject the new plan and retain the last valid plan as stale.
- Script/voice changed: block final generation until explicit reconfirmation.
- Missing media: fail before final render and list all missing ranges.
- Final duration outside tolerance: fail final validation and retain task artifacts for diagnosis.
- No stock-material fallback and no automatic voice-rate change.

## Non-goals

- Automatic voice-speed adjustment.
- Automatic repetition of the first six clips.
- Reintroducing Pexels, Pixabay, Coverr, LoomLoom, or other stock fallback into the Main Generator.
- Renaming public `six_clip_*` fields and modules.
- Completing Cloud Agent Flow/Canva implementation in this baseline change.
- Removing legacy rendering paths.
- Merging either Draft PR.

## Verification

Automated tests must cover:

- exact range calculation at 55, 60, 60.1, 63, 88, and 127 seconds;
- legacy six-plan JSON compatibility;
- no TTS call from Generate Script;
- explicit confirmation makes one full TTS call;
- confirmed preview reuse avoids a second TTS call;
- script/voice/rate/provider changes mark the plan stale;
- unchanged prompt/media edits do not mark narration stale;
- dynamic AI output count/range validation;
- six-card UI pagination and stable absolute keys;
- dynamic master-prompt batches;
- dynamic URL/upload indices and signed-query redaction;
- missing dynamic media fail-closed behavior;
- 63-second and 127-second rendering;
- final partial-source trimming;
- no stock provider call;
- no `>60` narration rejection;
- task history restore for legacy and dynamic plans;
- existing fixed-six regressions updated to the new compatibility contract;
- Python 3.11, Python 3.13, Ruff, Windows smoke, and the full test suite.

## Rollout gates

1. Commit this design spec to the existing feature branch.
2. User reviews and approves the written spec.
3. Create the detailed TDD implementation plan.
4. Update Draft PR #3 title/body to describe the planned dynamic extension while keeping it Draft.
5. Execute tasks only after the plan is reviewed.
6. Run focused tests after every task and the full verification matrix before user download testing.
7. Keep PR #3 unmerged until explicit user approval.
