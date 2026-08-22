from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services.six_clip_plan import build_script_generation_requirements


MAIN_SOURCE = Path("webui/Main.py").read_text(encoding="utf-8")


def _segments() -> list[SixClipSegment]:
    return [
        SixClipSegment(
            index=index,
            start_sec=(index - 1) * 10,
            end_sec=index * 10,
        )
        for index in range(1, 7)
    ]


def test_target_words_accepts_values_above_previous_400_limit():
    plan = SixClipPlan(target_words=100_000, segments=_segments())

    assert plan.target_words == 100_000
    requirements = build_script_generation_requirements(100_000)
    assert "100000" in requirements


def test_target_words_still_rejects_values_below_minimum():
    with pytest.raises(ValidationError):
        SixClipPlan(target_words=39, segments=_segments())

    with pytest.raises(ValueError, match="at least 40"):
        build_script_generation_requirements(39)


def test_target_words_ui_defaults_to_130_without_a_maximum():
    target_block = MAIN_SOURCE.split(
        "params.target_words = st.number_input(", 1
    )[1].split(
        '_set_runtime_config("ui", "target_words"', 1
    )[0]

    assert '"Target Words"' in target_block
    assert "min_value=40" in target_block
    assert "max_value=" not in target_block
    assert '"target_words", 130' in target_block
