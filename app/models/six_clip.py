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
    index: int = Field(ge=1, le=6)
    start_sec: int = Field(ge=0, le=50)
    end_sec: int = Field(ge=10, le=60)
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
        expected = SIX_CLIP_RANGES[self.index - 1]
        if (self.start_sec, self.end_sec) != expected:
            raise ValueError(
                f"clip {self.index} must use fixed range {expected[0]}-{expected[1]} seconds"
            )
        if self.media_kind and not self.media_path:
            raise ValueError("media_kind requires media_path")
        if self.media_path and not self.media_kind:
            raise ValueError("media_path requires media_kind")
        return self


class SixClipPlan(BaseModel):
    target_words: int = Field(default=130, ge=40, le=400)
    segments: list[SixClipSegment] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _validate_fixed_timeline(self):
        indexes = [segment.index for segment in self.segments]
        if indexes != [1, 2, 3, 4, 5, 6]:
            raise ValueError("segments must be ordered exactly 1 through 6")
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
