import json
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


def validate_six_clip_plan(plan: SixClipPlan) -> SixClipPlan:
    if len(plan.segments) != 6:
        raise ValueError("six-clip plan must contain exactly six segments")

    actual = [
        (segment.index, segment.start_sec, segment.end_sec)
        for segment in plan.segments
    ]
    expected = [
        (index, start, end)
        for index, (start, end) in enumerate(SIX_CLIP_RANGES, start=1)
    ]
    if actual != expected:
        raise ValueError("six-clip plan must use the fixed six-clip timeline")
    return plan


def parse_ai_clip_plan(text: str, target_words: int = 120) -> SixClipPlan:
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as exc:
        raise ValueError("six-clip AI response is not valid JSON") from exc

    clips = payload.get("clips") if isinstance(payload, dict) else None
    if not isinstance(clips, list) or len(clips) != 6:
        raise ValueError("six-clip AI response must contain exactly six clips")

    segments: list[SixClipSegment] = []
    for index, ((start, end), clip) in enumerate(zip(SIX_CLIP_RANGES, clips), start=1):
        if not isinstance(clip, dict):
            raise ValueError(f"clip {index} must be an object")
        segments.append(
            SixClipSegment(
                index=index,
                start_sec=start,
                end_sec=end,
                title=str(clip.get("title") or f"Clip {index}").strip(),
                narration_context=str(clip.get("narration_context") or "").strip(),
                video_prompt=str(clip.get("video_prompt") or "").strip(),
            )
        )

    return validate_six_clip_plan(
        SixClipPlan(target_words=int(target_words), segments=segments)
    )


def build_master_prompt(plan: SixClipPlan) -> str:
    validate_six_clip_plan(plan)
    blocks = [MASTER_PROMPT_HEADER]
    for segment in plan.segments:
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
    return "\n\n".join(blocks).strip()


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


def generate_six_clip_plan(
    video_script: str,
    language: str = "",
    target_words: int = 120,
    app_config=None,
) -> SixClipPlan:
    # Keep provider selection/retry behavior centralized in the existing LLM service.
    # This helper only defines the strict structured output contract for the six-clip UI.
    from app.services import llm

    prompt = build_six_clip_analysis_prompt(video_script, language)
    response = (
        llm._generate_response(prompt)
        if app_config is None
        else llm._generate_response(prompt, app_config=app_config)
    )
    return parse_ai_clip_plan(response, target_words=target_words)
