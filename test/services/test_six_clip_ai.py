import json

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


def test_analysis_prompt_requires_six_fixed_ranges_and_detailed_english_prompts():
    prompt = six_clip_plan.build_six_clip_analysis_prompt(
        "This is the full narration.",
        "English",
    )

    for time_range in (
        "0–10 seconds",
        "10–20 seconds",
        "20–30 seconds",
        "30–40 seconds",
        "40–50 seconds",
        "50–60 seconds",
    ):
        assert time_range in prompt
    assert "0–3 seconds, 3–6 seconds, and 6–10 seconds" in prompt
    assert "written in English" in prompt
    assert "American adults" in prompt
    assert "must not speak" in prompt


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
