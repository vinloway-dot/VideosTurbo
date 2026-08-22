import math

import pytest

from app.services.cloud_agent.errors import NarrationTooLongError
from app.services.cloud_agent.timing import calculate_adaptive_timing


@pytest.mark.parametrize(
    ("duration", "expected_speed", "expected_target"),
    [
        (55.0, 1.0, 60.0),
        (60.0, 1.0, 60.0),
        (63.0, 60.0 / 63.0, 63.0),
        (70.0, 60.0 / 70.0, 70.0),
    ],
)
def test_calculate_adaptive_timing(duration, expected_speed, expected_target):
    result = calculate_adaptive_timing(duration, min_playback_speed=0.85)

    assert result.audio_duration_seconds == duration
    assert result.canva_playback_speed == pytest.approx(expected_speed)
    assert result.target_final_duration_seconds == expected_target


def test_decimal_duration_is_not_ceiled_before_calculation():
    result = calculate_adaptive_timing(62.1, min_playback_speed=0.85)

    assert result.audio_duration_seconds == 62.1
    assert result.canva_playback_speed == pytest.approx(60.0 / 62.1)
    assert result.target_final_duration_seconds == 62.1


def test_required_speed_below_floor_is_rejected_with_actionable_error():
    with pytest.raises(NarrationTooLongError) as exc_info:
        calculate_adaptive_timing(71.0, min_playback_speed=0.85)

    assert exc_info.value.error_code == "NARRATION_TOO_LONG_FOR_SIX_CLIP"
    assert "71.000" in str(exc_info.value)
    assert "0.85" in str(exc_info.value)


@pytest.mark.parametrize("duration", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_invalid_audio_duration_is_rejected(duration):
    with pytest.raises(ValueError, match="audio_duration_seconds"):
        calculate_adaptive_timing(duration, min_playback_speed=0.85)


@pytest.mark.parametrize("base_duration", [0.0, -1.0, math.inf, math.nan])
def test_invalid_base_visual_duration_is_rejected(base_duration):
    with pytest.raises(ValueError, match="base_visual_duration_seconds"):
        calculate_adaptive_timing(
            60.0,
            base_visual_duration_seconds=base_duration,
            min_playback_speed=0.85,
        )


@pytest.mark.parametrize("minimum_speed", [0.0, -0.1, 1.01, math.inf, math.nan])
def test_invalid_minimum_playback_speed_is_rejected(minimum_speed):
    with pytest.raises(ValueError, match="min_playback_speed"):
        calculate_adaptive_timing(60.0, min_playback_speed=minimum_speed)
