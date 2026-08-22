import json

import pytest
from pydantic import ValidationError

from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services import six_clip_plan
from app.services.six_clip_plan import (
    GLOBAL_CHARACTER_RULES,
    build_master_prompt,
    parse_ai_clip_plan,
    validate_six_clip_plan,
)


def _segment(index: int) -> SixClipSegment:
    start = (index - 1) * 10
    end = index * 10
    return SixClipSegment(
        index=index,
        start_sec=start,
        end_sec=end,
        title=f"Scene {index}",
        narration_context=f"Narration {index}",
        video_prompt=(
            "Create a realistic 10-second vertical 9:16 video. "
            "0–3 seconds: opening action. 3–6 seconds: development. "
            "6–10 seconds: closing visual."
        ),
    )


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
    ranges = six_clip_plan.build_timeline_ranges(duration)

    assert len(ranges) == count
    assert ranges[-1] == last_range


def test_plan_keeps_legacy_six_clip_timeline_compatible():
    plan = SixClipPlan(target_words=130, segments=[_segment(i) for i in range(1, 7)])

    validate_six_clip_plan(plan)
    assert [(s.start_sec, s.end_sec) for s in plan.segments] == [
        (0, 10),
        (10, 20),
        (20, 30),
        (30, 40),
        (40, 50),
        (50, 60),
    ]
    assert plan.narration_duration_sec == 60.0
    assert plan.timeline_duration_sec == 60.0
    assert plan.slot_duration_sec == 10.0
    assert plan.narration_fingerprint == ""


def test_plan_accepts_dynamic_ranges_and_checks_current_fingerprint():
    ranges = six_clip_plan.build_timeline_ranges(63.0)
    plan = SixClipPlan(
        target_words=150,
        narration_duration_sec=63.0,
        timeline_duration_sec=63.0,
        narration_fingerprint="voice-fingerprint",
        segments=[
            SixClipSegment(
                index=index,
                start_sec=start,
                end_sec=end,
                title=f"Scene {index}",
            )
            for index, (start, end) in enumerate(ranges, start=1)
        ],
    )

    assert len(plan.segments) == 7
    assert six_clip_plan.is_timeline_current(plan, "voice-fingerprint")
    assert not six_clip_plan.is_timeline_current(plan, "")
    assert not six_clip_plan.is_timeline_current(plan, "changed")


def test_plan_rejects_missing_or_shifted_segments():
    with pytest.raises(ValidationError):
        SixClipPlan(target_words=130, segments=[_segment(i) for i in range(1, 6)])

    shifted = [_segment(i) for i in range(1, 7)]
    shifted[2] = shifted[2].model_copy(update={"start_sec": 21})
    with pytest.raises(ValueError, match="range 20.*30 seconds"):
        SixClipPlan(target_words=130, segments=shifted)


def test_configured_maximum_rejects_required_clip_count():
    with pytest.raises(ValueError, match="requires 13 clips.*maximum is 12"):
        six_clip_plan.build_timeline_ranges(127.0, maximum_clip_count=12)


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_timeline_ranges_reject_invalid_duration(duration):
    with pytest.raises(ValueError, match="finite positive"):
        six_clip_plan.build_timeline_ranges(duration)


def test_master_prompt_contains_global_rules_and_all_current_clip_values():
    plan = SixClipPlan(target_words=140, segments=[_segment(i) for i in range(1, 7)])
    plan.segments[3].video_prompt = "UPDATED CLIP FOUR PROMPT"

    master = build_master_prompt(plan)

    assert master.startswith("Create six videos, each 10 seconds long")
    assert GLOBAL_CHARACTER_RULES in master
    assert "CLIP 1 — Scene 1" in master
    assert "CLIP 6 — Scene 6" in master
    assert "Narration context:\nNarration 4" in master
    assert "Video Prompt:\n\nUPDATED CLIP FOUR PROMPT" in master


def test_parse_ai_clip_plan_accepts_fenced_json_and_normalizes_timeline():
    payload = {
        "clips": [
            {
                "title": f"Topic {i}",
                "narration_context": f"Part {i}",
                "video_prompt": (
                    "Create a realistic 10-second vertical 9:16 video. "
                    "0–3 seconds: hook. 3–6 seconds: action. "
                    "6–10 seconds: finish."
                ),
            }
            for i in range(1, 7)
        ]
    }

    plan = parse_ai_clip_plan(f"```json\n{json.dumps(payload)}\n```", target_words=125)

    assert plan.target_words == 125
    assert [segment.index for segment in plan.segments] == [1, 2, 3, 4, 5, 6]
    assert plan.segments[0].start_sec == 0
    assert plan.segments[-1].end_sec == 60


def test_parse_ai_clip_plan_rejects_wrong_clip_count():
    payload = {
        "clips": [
            {"title": "only one", "narration_context": "x", "video_prompt": "y"}
        ]
    }
    with pytest.raises(ValueError, match="exactly six"):
        parse_ai_clip_plan(json.dumps(payload), target_words=130)
