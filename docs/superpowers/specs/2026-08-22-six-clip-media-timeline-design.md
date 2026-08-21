# Six-Clip Media Timeline Design

## Goal

Replace the Main Generator stock-material timeline with a fixed six-segment, 60-second visual timeline while preserving the existing narration, subtitle, BGM, task history, Music Batch, and backend stock-provider code.

## User-facing flow

1. Section 1 keeps the existing Video Subject, Video Script, Video Keywords, and Generate Script & Keywords with AI flow.
2. Add Target Words. Script generation should target the requested word count and begin with a strong 0–3 second hook.
3. Section 2 contains exactly six editable clips: 0–10, 10–20, 20–30, 30–40, 40–50, 50–60 seconds.
4. Each clip contains an editable narration context, an editable detailed English video prompt, and exactly one media source: Direct URL or Upload.
5. Direct URLs are HTTP/HTTPS media URLs. They do not need to end in a file extension. Google Flow signed URLs such as `https://flow-content.google/video/...?...Signature=...` are valid when the HTTP response is actual supported media.
6. Imported URL media is downloaded immediately, validated by response/content rather than URL suffix, and then rendered from the local copy. Signed URL query strings must never be written to task history or logs.
7. Section 3 is a live Master Prompt built from the current Section 2 values. It starts with the fixed global character rules and then lists CLIP 1 through CLIP 6 with Narration context and Video Prompt.
8. Final rendering requires media for all six clips. If any clip is missing media, rendering fails closed before TTS/paid services and reports the missing clip numbers/time ranges.
9. The Main Generator no longer uses Pexels, Pixabay, Coverr, LoomLoom, Video/Image/Mixed, or stock fallback in this branch. Backend stock code remains intact for Music Batch and compatibility.

## Script rules

- Section 1 remains the single source of truth for TTS and subtitles.
- Target Words controls generation length but is not a hard post-generation truncation.
- The generated script must begin with a hook intended for the first 0–3 seconds.
- Section 2 narration contexts are derived from Section 1 in original narrative order; they must not introduce contradictory facts.
- Section 2 video prompts are detailed English 10-second generation prompts and should describe sub-ranges such as 0–3, 3–6, and 6–10 seconds.

## Timeline rules

- Exactly six visual segments, each exactly 10 seconds.
- Final order is fixed: 1 → 2 → 3 → 4 → 5 → 6.
- Uploaded/imported videos have source audio removed.
- Video longer than 10 seconds is trimmed to the first 10 seconds.
- Video shorter than 10 seconds loops until exactly 10 seconds.
- Images become exactly 10-second clips using the existing image-motion/Ken Burns implementation.
- Every segment is normalized to the selected final aspect ratio.
- The visual timeline is exactly 60 seconds.
- If narration audio is longer than 60 seconds, stop with an actionable error instead of cutting speech.
- If narration is shorter than 60 seconds, keep the full 60-second visual timeline. Narration ends naturally; BGM may continue to the end.

## Data model

Introduce focused six-clip models rather than overloading stock material fields:

- `SixClipSegment`: index, start_sec, end_sec, title, narration_context, video_prompt, media_kind, media_path.
- `SixClipPlan`: target_words and exactly six ordered segments.
- `VideoParams.six_clip_mode`: backwards-compatible boolean default false.
- `VideoParams.six_clip_plan`: optional `SixClipPlan`.

No original signed media URL is persisted in `VideoParams` or task artifacts after successful import.

## Modules

- `app/models/six_clip.py`: timeline models and constants.
- `app/services/six_clip_plan.py`: AI prompt contract, response parsing, validation, master prompt assembly.
- `app/services/six_clip_media.py`: URL import, media type validation, upload persistence, readiness checks, redaction.
- `app/services/six_clip_render.py`: deterministic six-segment preparation and 60-second concatenation.
- `webui/six_clip_timeline.py`: Section 2 and Section 3 Streamlit UI/state.
- `webui/Main.py`: small integration changes only.
- `app/services/task.py`: route six-clip tasks around the stock-material path.

## Error handling

- Missing clip media: fail before script/TTS/material services are consumed.
- Unsupported URL scheme or non-media HTTP response: reject import.
- Oversized/failed download: reject import and leave the clip Missing.
- Expired signed URL: show import failure; never retry during final rendering.
- Audio >60 seconds: fail at audio stage with measured duration and guidance to reduce Target Words or increase Voice Rate.
- Stock-material downloader must never be called in six-clip mode.

## Compatibility

- Existing backend stock-material services remain untouched unless a narrowly-scoped compatibility change is required.
- Music Batch retains its existing material-source behavior.
- API/CLI tasks with `six_clip_mode=false` continue using the existing pipeline.
- This feature is developed on `feature/six-clip-media-timeline`, based on the verified `feature/material-type-mixed` commit, and is not merged back unless explicitly requested later.

## Verification

Automated tests must cover exact six-clip structure, timestamp rules, master prompt live assembly, Google-Flow-style no-extension media URLs, signed-query redaction, fail-closed missing media, no stock-provider calls, fixed order, video trim/loop, image duration, 60-second output, and audio >60-second rejection. CI must pass Python 3.11, Python 3.13, Ruff, and Windows smoke tests before user download testing.