import json
import math
import re

from app.models.six_clip import SIX_CLIP_RANGES, SixClipPlan, SixClipSegment


GLOBAL_CHARACTER_RULES = """--------
GLOBAL CHARACTER RULES FOR ALL CLIPS

Whenever human characters appear:

All characters must be American adults.
Characters must not speak, lip-sync, or appear to say any words.
Communication must happen only through natural facial expressions, body language, gestures, and actions appropriate to the scene.
Avoid exaggerated acting. Keep all reactions realistic and documentary-like.
No dialogue, no visible speech, and no talking directly to the camera.
--------"""

MASTER_PROMPT_HEADER = (
    "Create six videos, each 10 seconds long, using the information below.\n\n"
    f"{GLOBAL_CHARACTER_RULES}"
)


def _strip_code_fence(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def build_timeline_ranges(
    narration_duration_sec: float,
    *,
    slot_duration_sec: float = 10.0,
    minimum_clip_count: int = 6,
    maximum_clip_count: int = 0,
) -> tuple[tuple[float, float], ...]:
    duration = float(narration_duration_sec)
    slot_duration = float(slot_duration_sec)
    minimum_count = int(minimum_clip_count)
    maximum_count = int(maximum_clip_count)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("narration duration must be a finite positive number")
    if not math.isfinite(slot_duration) or slot_duration <= 0:
        raise ValueError("slot duration must be a finite positive number")
    if minimum_count < 1:
        raise ValueError("minimum clip count must be positive")
    if maximum_count < 0:
        raise ValueError("maximum clip count cannot be negative")

    clip_count = max(minimum_count, math.ceil(duration / slot_duration))
    if maximum_count > 0 and clip_count > maximum_count:
        raise ValueError(
            f"narration requires {clip_count} clips; configured maximum is "
            f"{maximum_count}"
        )
    timeline_duration = max(minimum_count * slot_duration, duration)
    return tuple(
        (
            index * slot_duration,
            min((index + 1) * slot_duration, timeline_duration),
        )
        for index in range(clip_count)
    )


def validate_timeline_plan(plan: SixClipPlan) -> SixClipPlan:
    expected_ranges = build_timeline_ranges(
        plan.narration_duration_sec,
        slot_duration_sec=plan.slot_duration_sec,
    )
    actual = [
        (segment.index, segment.start_sec, segment.end_sec)
        for segment in plan.segments
    ]
    expected = [
        (index, start, end)
        for index, (start, end) in enumerate(expected_ranges, start=1)
    ]
    if actual != expected:
        raise ValueError("timeline plan must use the narration-driven ranges")
    return plan


def validate_six_clip_plan(plan: SixClipPlan) -> SixClipPlan:
    return validate_timeline_plan(plan)


def is_timeline_current(
    plan: SixClipPlan,
    narration_fingerprint: str,
) -> bool:
    expected = str(narration_fingerprint or "").strip()
    return bool(expected and plan.narration_fingerprint == expected)


def parse_ai_clip_plan(
    text: str,
    target_words: int = 130,
    *,
    timeline_ranges: tuple[tuple[float, float], ...] | None = None,
    narration_duration_sec: float = 60.0,
    narration_fingerprint: str = "",
) -> SixClipPlan:
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as exc:
        raise ValueError("six-clip AI response is not valid JSON") from exc

    clips = payload.get("clips") if isinstance(payload, dict) else None
    expected_ranges = timeline_ranges or SIX_CLIP_RANGES
    if not isinstance(clips, list) or len(clips) != len(expected_ranges):
        if timeline_ranges is None:
            raise ValueError("six-clip AI response must contain exactly six clips")
        raise ValueError("AI response must contain exactly the requested ranges")

    segments: list[SixClipSegment] = []
    for index, ((start, end), clip) in enumerate(zip(expected_ranges, clips), start=1):
        if not isinstance(clip, dict):
            raise ValueError(f"clip {index} must be an object")

        if timeline_ranges is not None:
            try:
                supplied_index = int(clip["index"])
                supplied_start = float(clip["start_sec"])
                supplied_end = float(clip["end_sec"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "AI response must use the exact requested ranges"
                ) from exc
            if not (
                supplied_index == index
                and math.isclose(supplied_start, start, abs_tol=1e-6)
                and math.isclose(supplied_end, end, abs_tol=1e-6)
            ):
                raise ValueError("AI response must use the exact requested ranges")

        video_prompt = str(clip.get("video_prompt") or "").strip()
        slot_duration = float(end) - float(start)
        if timeline_ranges is not None and slot_duration < 10.0 - 1e-6:
            visible_seconds = f"{slot_duration:g}"
            trim_rule = (
                "Complete the required visual action within the first "
                f"{visible_seconds} seconds; footage after that will be trimmed."
            )
            if trim_rule not in video_prompt:
                video_prompt = f"{video_prompt}\n\n{trim_rule}".strip()

        segments.append(
            SixClipSegment(
                index=index,
                start_sec=start,
                end_sec=end,
                title=str(clip.get("title") or f"Clip {index}").strip(),
                narration_context=str(clip.get("narration_context") or "").strip(),
                video_prompt=video_prompt,
            )
        )

    duration = float(narration_duration_sec)
    return validate_timeline_plan(
        SixClipPlan(
            target_words=int(target_words),
            narration_duration_sec=duration,
            timeline_duration_sec=max(60.0, duration),
            narration_fingerprint=str(narration_fingerprint or "").strip(),
            segments=segments,
        )
    )


def _master_prompt_header(
    plan: SixClipPlan,
    batch: list[SixClipSegment],
) -> str:
    if len(plan.segments) == 6:
        return MASTER_PROMPT_HEADER
    first = batch[0]
    last = batch[-1]
    return (
        f"Create clips {first.index}–{last.index} of {len(plan.segments)} videos, "
        "each up to 10 seconds long, covering "
        f"{first.start_sec:g}–{last.end_sec:g} seconds of a "
        f"{plan.timeline_duration_sec:g}-second timeline.\n\n"
        f"{GLOBAL_CHARACTER_RULES}"
    )


def build_master_prompt_batches(
    plan: SixClipPlan,
    batch_size: int = 6,
) -> tuple[str, ...]:
    validate_timeline_plan(plan)
    if batch_size < 1 or batch_size > 6:
        raise ValueError("batch_size must be between 1 and 6")

    prompts = []
    for batch_start in range(0, len(plan.segments), batch_size):
        batch = plan.segments[batch_start : batch_start + batch_size]
        blocks = [_master_prompt_header(plan, batch)]
        for segment in batch:
            title = segment.title or f"Clip {segment.index}"
            blocks.append(
                "\n\n".join(
                    [
                        f"CLIP {segment.index} — {title}",
                        f"Narration context:\n{segment.narration_context}",
                        f"Video Prompt:\n\n{segment.video_prompt}",
                    ]
                )
            )
        prompts.append("\n\n".join(blocks).strip())
    return tuple(prompts)


def build_master_prompt(plan: SixClipPlan) -> str:
    return "\n\n======== NEXT BATCH ========\n\n".join(
        build_master_prompt_batches(plan)
    )


def build_script_generation_requirements(
    target_words: int,
    user_requirements: str = "",
) -> str:
    try:
        target = int(target_words)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_words must be an integer") from exc
    if target < 40 or target > 400:
        raise ValueError("target_words must be between 40 and 400")

    requirements = [
        f"Write approximately {target} words so the narration is suitable for about 60 seconds.",
        (
            "The first 0–3 seconds must function as a strong hook that immediately "
            "creates curiosity or communicates the most compelling point."
        ),
        "Keep the hook as part of the natural narration; do not label it as a hook.",
    ]
    custom = str(user_requirements or "").strip()
    if custom:
        requirements.append(custom)
    return "\n".join(requirements)


def build_six_clip_analysis_prompt(video_script: str, language: str = "") -> str:
    script = str(video_script or "").strip()
    if not script:
        raise ValueError("video_script is required")

    language_note = str(language or "auto-detect").strip() or "auto-detect"
    return f"""
# Role: Six-Clip Visual Director

Analyze the narration below and divide it into exactly six chronological visual sections for a 60-second short video.

## Timeline
- Clip 1: 0–10 seconds. The narration context must contain the opening hook intended for 0–3 seconds.
- Clip 2: 10–20 seconds.
- Clip 3: 20–30 seconds.
- Clip 4: 30–40 seconds.
- Clip 5: 40–50 seconds.
- Clip 6: 50–60 seconds.

## Rules
1. Preserve the original narration meaning and chronological order. Do not invent contradictory facts.
2. `narration_context` should contain only the portion of the supplied narration that belongs to that clip.
3. Every `video_prompt` must be written in English and be ready for a 10-second AI video generator.
4. Every `video_prompt` must be detailed and visually concrete, preferably using 0–3 seconds, 3–6 seconds, and 6–10 seconds beats.
5. If human characters appear, they must be American adults. They must not speak, lip-sync, or appear to say any words. Communication happens through realistic facial expressions, body language, gestures, and actions.
6. No dialogue, visible speech, talking directly to camera, captions, labels, logos, or watermarks unless the narration specifically requires visible text as subject matter.
7. Keep acting realistic and documentary-like rather than exaggerated.
8. Return raw JSON only. No markdown and no commentary.

## Output JSON
{{
  "clips": [
    {{"title": "short English title", "narration_context": "...", "video_prompt": "..."}},
    {{"title": "short English title", "narration_context": "...", "video_prompt": "..."}},
    {{"title": "short English title", "narration_context": "...", "video_prompt": "..."}},
    {{"title": "short English title", "narration_context": "...", "video_prompt": "..."}},
    {{"title": "short English title", "narration_context": "...", "video_prompt": "..."}},
    {{"title": "short English title", "narration_context": "...", "video_prompt": "..."}}
  ]
}}

## Narration language
{language_note}

## Full narration
{script}
""".strip()


def partition_narration_cues(
    cues: tuple[tuple[float, float, str], ...],
    timeline_ranges: tuple[tuple[float, float], ...],
) -> tuple[str, ...]:
    """Assign each narration cue to one slot using its midpoint."""
    chunks: list[list[str]] = [[] for _ in timeline_ranges]
    if not timeline_ranges:
        return ()

    for cue_start, cue_end, text in cues:
        midpoint = (float(cue_start) + float(cue_end)) / 2
        for position, (range_start, range_end) in enumerate(timeline_ranges):
            is_last = position == len(timeline_ranges) - 1
            if range_start <= midpoint < range_end or (
                is_last and math.isclose(midpoint, range_end, abs_tol=1e-6)
            ):
                normalized = str(text or "").strip()
                if normalized:
                    chunks[position].append(normalized)
                break
    return tuple(" ".join(chunk) for chunk in chunks)


def _format_seconds(value: float) -> str:
    return f"{float(value):g}"


def build_timeline_analysis_prompt(
    video_script: str,
    timeline_ranges: tuple[tuple[float, float], ...],
    language: str = "",
    narration_chunks: tuple[str, ...] | None = None,
) -> str:
    script = str(video_script or "").strip()
    if not script:
        raise ValueError("video_script is required")
    if not timeline_ranges:
        raise ValueError("timeline_ranges are required")
    if narration_chunks is not None and len(narration_chunks) != len(timeline_ranges):
        raise ValueError("narration_chunks must match timeline_ranges")

    timeline_lines = []
    output_lines = []
    for index, (start, end) in enumerate(timeline_ranges, start=1):
        start_text = _format_seconds(start)
        end_text = _format_seconds(end)
        note = " The narration context must contain the opening hook intended for 0–3 seconds." if index == 1 else ""
        if narration_chunks is not None and narration_chunks[index - 1]:
            note += f' Timed narration: "{narration_chunks[index - 1]}"'
        if float(end) - float(start) < 10.0 - 1e-6:
            note += (
                f" Complete the required visual action within the first "
                f"{_format_seconds(float(end) - float(start))} seconds; footage "
                "after that will be trimmed."
            )
        timeline_lines.append(
            f"- Clip {index}: {start_text}–{end_text} seconds.{note}"
        )
        output_lines.append(
            "    "
            + json.dumps(
                {
                    "index": index,
                    "start_sec": float(start),
                    "end_sec": float(end),
                    "title": "short English title",
                    "narration_context": "...",
                    "video_prompt": "...",
                },
                ensure_ascii=False,
            )
        )

    language_note = str(language or "auto-detect").strip() or "auto-detect"
    return f"""
# Role: Dynamic Timeline Visual Director

Analyze the narration below into exactly {len(timeline_ranges)} chronological visual sections using the requested ranges.

## Timeline
{chr(10).join(timeline_lines)}

## Rules
1. Preserve the original narration meaning and chronological order. Do not invent contradictory facts.
2. `narration_context` should contain only the narration belonging to that exact requested range.
3. Every `video_prompt` must be written in English and ready for a 10-second AI video generator.
4. Every `video_prompt` must be detailed and visually concrete, preferably using 0–3 seconds, 3–6 seconds, and 6–10 seconds beats.
5. If human characters appear, they must be American adults and must not speak, lip-sync, or appear to say words.
6. No dialogue, visible speech, talking directly to camera, captions, labels, logos, or watermarks unless visible text is the subject.
7. Return each index, start_sec, and end_sec exactly as requested. Do not reorder, omit, merge, or add ranges.
8. Return raw JSON only. No markdown and no commentary.

## Output JSON
{{
  "clips": [
{(',' + chr(10)).join(output_lines)}
  ]
}}

## Narration language
{language_note}

## Full narration
{script}
""".strip()


def generate_six_clip_plan(
    video_script: str,
    language: str = "",
    target_words: int = 130,
    app_config=None,
    *,
    timeline_ranges: tuple[tuple[float, float], ...] | None = None,
    narration_duration_sec: float = 60.0,
    narration_fingerprint: str = "",
    subtitle_cues: tuple[tuple[float, float, str], ...] | None = None,
) -> SixClipPlan:
    # Keep provider selection/retry behavior centralized in the existing LLM service.
    # This helper only defines the strict structured output contract for the six-clip UI.
    from app.services import llm

    if timeline_ranges is None:
        prompt = build_six_clip_analysis_prompt(video_script, language)
    else:
        narration_chunks = (
            partition_narration_cues(subtitle_cues, timeline_ranges)
            if subtitle_cues
            else None
        )
        prompt = build_timeline_analysis_prompt(
            video_script,
            timeline_ranges,
            language=language,
            narration_chunks=narration_chunks,
        )
    response = (
        llm._generate_response(prompt)
        if app_config is None
        else llm._generate_response(prompt, app_config=app_config)
    )
    return parse_ai_clip_plan(
        response,
        target_words=target_words,
        timeline_ranges=timeline_ranges,
        narration_duration_sec=narration_duration_sec,
        narration_fingerprint=narration_fingerprint,
    )
