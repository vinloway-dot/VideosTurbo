import json

import pytest

from app.services import llm, six_clip_plan


def test_script_requirements_include_target_words_and_opening_hook():
    text = six_clip_plan.build_script_generation_requirements(
        145,
        "Use a neutral educational tone.",
    )

    assert "approximately 145 words" in text
    assert "first 0–3 seconds" in text
    assert "strong hook" in text
    assert "Use a neutral educational tone." in text


def test_analysis_prompt_uses_supplied_dynamic_ranges_and_partial_slot_rule():
    prompt = six_clip_plan.build_timeline_analysis_prompt(
        "This is the full narration.",
        six_clip_plan.build_timeline_ranges(63.0),
        language="English",
    )

    for time_range in (
        "0–10 seconds",
        "10–20 seconds",
        "20–30 seconds",
        "30–40 seconds",
        "40–50 seconds",
        "50–60 seconds",
        "60–63 seconds",
    ):
        assert time_range in prompt
    assert "0–3 seconds, 3–6 seconds, and 6–10 seconds" in prompt
    assert "written in English" in prompt
    assert "American adults" in prompt
    assert "must not speak" in prompt
    assert "first 3 seconds" in prompt


def test_partition_narration_cues_uses_each_cue_midpoint_once():
    ranges = ((0.0, 10.0), (10.0, 20.0), (20.0, 30.0))
    cues = (
        (0.0, 4.0, "Opening"),
        (8.0, 12.0, "Boundary"),
        (20.0, 30.0, "Ending"),
    )

    assert six_clip_plan.partition_narration_cues(cues, ranges) == (
        "Opening",
        "Boundary",
        "Ending",
    )


def test_generate_six_clip_plan_uses_existing_llm_provider_path(monkeypatch):
    response = json.dumps(
        {
            "clips": [
                {
                    "title": f"Clip {index}",
                    "narration_context": f"Narration {index}",
                    "video_prompt": (
                        "Create a realistic 10-second video. "
                        "0–3 seconds: open. 3–6 seconds: continue. "
                        "6–10 seconds: finish."
                    ),
                }
                for index in range(1, 7)
            ]
        }
    )
    calls = []

    def fake_generate_response(prompt, app_config=None):
        calls.append((prompt, app_config))
        return response

    monkeypatch.setattr(llm, "_generate_response", fake_generate_response)

    plan = six_clip_plan.generate_six_clip_plan(
        "Narration text",
        language="English",
        target_words=150,
        app_config={"llm_provider": "openai"},
    )

    assert len(calls) == 1
    assert calls[0][1] == {"llm_provider": "openai"}
    assert plan.target_words == 150
    assert len(plan.segments) == 6


def test_generate_dynamic_plan_validates_exact_ai_ranges(monkeypatch):
    ranges = six_clip_plan.build_timeline_ranges(63.0)
    response = json.dumps(
        {
            "clips": [
                {
                    "index": index,
                    "start_sec": start,
                    "end_sec": end,
                    "title": f"Clip {index}",
                    "narration_context": f"Narration {index}",
                    "video_prompt": f"Prompt {index}",
                }
                for index, (start, end) in enumerate(ranges, start=1)
            ]
        }
    )
    monkeypatch.setattr(llm, "_generate_response", lambda *args, **kwargs: response)

    plan = six_clip_plan.generate_six_clip_plan(
        "Narration text",
        language="English",
        target_words=150,
        timeline_ranges=ranges,
        narration_duration_sec=63.0,
        narration_fingerprint="voice-fingerprint",
    )

    assert len(plan.segments) == 7
    assert plan.segments[-1].end_sec == 63.0
    assert "first 3 seconds" in plan.segments[-1].video_prompt
    assert plan.narration_fingerprint == "voice-fingerprint"


def test_dynamic_plan_rejects_reordered_ai_ranges(monkeypatch):
    ranges = six_clip_plan.build_timeline_ranges(63.0)
    clips = [
        {
            "index": index,
            "start_sec": start,
            "end_sec": end,
            "title": f"Clip {index}",
            "narration_context": f"Narration {index}",
            "video_prompt": f"Prompt {index}",
        }
        for index, (start, end) in enumerate(ranges, start=1)
    ]
    clips[0], clips[1] = clips[1], clips[0]
    monkeypatch.setattr(
        llm,
        "_generate_response",
        lambda *args, **kwargs: json.dumps({"clips": clips}),
    )

    with pytest.raises(ValueError, match="requested ranges"):
        six_clip_plan.generate_six_clip_plan(
            "Narration text",
            timeline_ranges=ranges,
            narration_duration_sec=63.0,
        )
