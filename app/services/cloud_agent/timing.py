import math
from dataclasses import dataclass

@dataclass(frozen=True)
class AdaptiveTiming:
    audio_duration_seconds: float
    canva_playback_speed: float
    target_final_duration_seconds: float


def _require_positive_finite(name: str, value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return normalized


def calculate_adaptive_timing(
    audio_duration_seconds: float,
    *,
    base_visual_duration_seconds: float = 60.0,
    min_playback_speed: float = 0.85,
) -> AdaptiveTiming:
    duration = _require_positive_finite(
        "audio_duration_seconds", audio_duration_seconds
    )
    base_duration = _require_positive_finite(
        "base_visual_duration_seconds", base_visual_duration_seconds
    )
    minimum_speed = float(min_playback_speed)
    if not math.isfinite(minimum_speed) or not 0 < minimum_speed <= 1:
        raise ValueError("min_playback_speed must be finite and within (0, 1]")

    if duration <= base_duration:
        playback_speed = 1.0
        target_duration = base_duration
    else:
        playback_speed = base_duration / duration
        target_duration = duration

    return AdaptiveTiming(
        audio_duration_seconds=duration,
        canva_playback_speed=playback_speed,
        target_final_duration_seconds=target_duration,
    )
