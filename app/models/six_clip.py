import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


SIX_CLIP_RANGES: tuple[tuple[int, int], ...] = (
    (0, 10),
    (10, 20),
    (20, 30),
    (30, 40),
    (40, 50),
    (50, 60),
)

SUPPORTED_MEDIA_KINDS = {"", "video", "image"}


class SixClipSegment(BaseModel):
    index: int = Field(ge=1)
    start_sec: float = Field(ge=0, allow_inf_nan=False)
    end_sec: float = Field(gt=0, allow_inf_nan=False)
    title: str = Field(default="", max_length=200)
    narration_context: str = Field(default="", max_length=4000)
    video_prompt: str = Field(default="", max_length=12000)
    media_kind: Literal["", "video", "image"] = ""
    media_path: str = Field(default="", max_length=4096)

    @field_validator("title", "narration_context", "video_prompt", "media_path")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _validate_range(self):
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        if self.media_kind and not self.media_path:
            raise ValueError("media_kind requires media_path")
        if self.media_path and not self.media_kind:
            raise ValueError("media_path requires media_kind")
        return self


class SixClipPlan(BaseModel):
    target_words: int = Field(default=130, ge=40, le=400)
    narration_duration_sec: float = Field(
        default=60.0,
        gt=0,
        allow_inf_nan=False,
    )
    timeline_duration_sec: float = Field(
        default=60.0,
        gt=0,
        allow_inf_nan=False,
    )
    slot_duration_sec: float = Field(
        default=10.0,
        gt=0,
        allow_inf_nan=False,
    )
    narration_fingerprint: str = Field(default="", max_length=256)
    segments: list[SixClipSegment] = Field(min_length=6)

    @model_validator(mode="after")
    def _validate_timeline(self):
        indexes = [segment.index for segment in self.segments]
        expected_indexes = list(range(1, len(self.segments) + 1))
        if indexes != expected_indexes:
            raise ValueError("segments must use consecutive indexes starting at 1")

        expected_timeline = max(
            6 * self.slot_duration_sec,
            self.narration_duration_sec,
        )
        if not math.isclose(
            self.timeline_duration_sec,
            expected_timeline,
            abs_tol=1e-6,
        ):
            raise ValueError(
                "timeline_duration_sec must match the narration-driven timeline"
            )

        expected_count = max(
            6,
            math.ceil(self.narration_duration_sec / self.slot_duration_sec),
        )
        if len(self.segments) != expected_count:
            raise ValueError(
                f"timeline requires exactly {expected_count} ordered segments"
            )

        for segment in self.segments:
            expected_start = (segment.index - 1) * self.slot_duration_sec
            expected_end = min(
                segment.index * self.slot_duration_sec,
                self.timeline_duration_sec,
            )
            if not (
                math.isclose(segment.start_sec, expected_start, abs_tol=1e-6)
                and math.isclose(segment.end_sec, expected_end, abs_tol=1e-6)
            ):
                raise ValueError(
                    f"clip {segment.index} must use range "
                    f"{expected_start:g}-{expected_end:g} seconds"
                )
        return self


def empty_six_clip_plan(target_words: int = 130) -> SixClipPlan:
    return SixClipPlan(
        target_words=target_words,
        segments=[
            SixClipSegment(
                index=index,
                start_sec=start,
                end_sec=end,
            )
            for index, (start, end) in enumerate(SIX_CLIP_RANGES, start=1)
        ],
    )
