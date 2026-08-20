from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models.schema import ImageMotion, MaterialType


class SongStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    retrying = "retrying"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class BatchStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    completed_with_failures = "completed_with_failures"
    failed = "failed"
    interrupted = "interrupted"
    needs_reencode_confirmation = "needs_reencode_confirmation"


class SortMode(str, Enum):
    filename = "filename"
    added = "added"


class SongOverride(BaseModel):
    video_script: str | None = None
    video_keywords: list[str] | None = None
    stock_sources: list[str] | None = None
    material_type: MaterialType | None = None
    image_duration: int | None = Field(default=None, ge=1, le=30)
    image_motion: ImageMotion | None = None
    video_clip_duration: int | None = Field(default=None, ge=1)
    video_concat_mode: str | None = None
    video_transition_mode: str | None = None
    video_clip_speed: float | None = Field(default=None, gt=0)


class BatchSettings(BaseModel):
    output_root: str
    video_script: str = ""
    video_keywords: list[str] = Field(default_factory=list)
    stock_sources: list[str] = Field(default_factory=lambda: ["pexels"])
    material_type: MaterialType = MaterialType.video
    image_duration: int = Field(default=8, ge=1, le=30)
    image_motion: ImageMotion = ImageMotion.random
    video_aspect: str = "16:9"
    video_concat_mode: str = "random"
    video_transition_mode: str | None = None
    video_clip_duration: int = Field(default=8, ge=1)
    video_clip_speed: float = Field(default=1.0, gt=0)
    video_encoder: str = "libx264"
    retry_count: int = Field(default=2, ge=0, le=10)
    parallel_jobs: int = Field(default=1, ge=1, le=4)
    sort_mode: SortMode = SortMode.filename
    avoid_reusing_clips: bool = False
    combine_all: bool = False


class SongItem(BaseModel):
    source_path: str
    added_index: int = Field(ge=0)
    override: SongOverride | None = None
    status: SongStatus = SongStatus.pending
    attempts: int = Field(default=0, ge=0)
    progress: int = Field(default=0, ge=0, le=100)
    output_path: str | None = None
    latest_error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    gpu_index: int | None = Field(default=None, ge=0)


class BatchState(BaseModel):
    batch_id: str
    batch_dir: str
    settings: BatchSettings
    songs: list[SongItem]
    status: BatchStatus = BatchStatus.pending
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None
    fatal_error: str | None = None
    compilation_status: str | None = None
    compilation_path: str | None = None
    compilation_members: list[str] = Field(default_factory=list)
    compilation_error: str | None = None
    used_clips: dict[str, list[str]] = Field(default_factory=dict)

    @classmethod
    def new_for_test(
        cls,
        status: BatchStatus | str = BatchStatus.pending,
        song_statuses: list[SongStatus] | None = None,
    ) -> "BatchState":
        statuses = song_statuses or [SongStatus.pending]
        songs = [
            SongItem(
                source_path=f"song-{index + 1}.mp3",
                added_index=index,
                status=song_status,
            )
            for index, song_status in enumerate(statuses)
        ]
        return cls(
            batch_id="test-batch",
            batch_dir=".",
            settings=BatchSettings(output_root="."),
            songs=songs,
            status=BatchStatus(status),
        )


def resolve_song_settings(
    batch_settings: BatchSettings, song: SongItem
) -> dict[str, Any]:
    """Resolve effective settings for one song without mutating batch globals."""

    resolved: dict[str, Any] = {
        "video_script": batch_settings.video_script,
        "video_keywords": list(batch_settings.video_keywords),
        "stock_sources": list(batch_settings.stock_sources),
        "material_type": batch_settings.material_type,
        "image_duration": batch_settings.image_duration,
        "image_motion": batch_settings.image_motion,
        "video_aspect": batch_settings.video_aspect,
        "video_concat_mode": batch_settings.video_concat_mode,
        "video_transition_mode": batch_settings.video_transition_mode,
        "video_clip_duration": batch_settings.video_clip_duration,
        "video_clip_speed": batch_settings.video_clip_speed,
        "video_encoder": batch_settings.video_encoder,
        "avoid_reusing_clips": batch_settings.avoid_reusing_clips,
    }

    if song.override is None:
        return resolved

    for field_name, value in song.override.model_dump().items():
        if value is not None:
            resolved[field_name] = value

    return resolved