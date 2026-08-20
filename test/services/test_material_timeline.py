from inspect import signature
from pathlib import Path

from app.models.schema import MaterialType, VideoConcatMode
from app.services import stock_materials
from app.services import video as video_service


def test_mixed_mode_keeps_alternating_timeline_even_when_user_requests_random():
    assert stock_materials.effective_concat_mode(
        MaterialType.mixed,
        VideoConcatMode.random,
    ) == VideoConcatMode.sequential
    assert stock_materials.effective_concat_mode(
        MaterialType.video,
        VideoConcatMode.random,
    ) == VideoConcatMode.random


def test_material_clip_duration_overrides_keep_image_and_video_durations_separate(tmp_path):
    video_path = str(tmp_path / "stock-video.mp4")
    image_path = str(tmp_path / "stock-image-clips" / "image-clip-001.mp4")

    overrides = stock_materials.build_clip_duration_overrides(
        [video_path, image_path],
        material_type=MaterialType.mixed,
        video_clip_duration=3,
        image_duration=8,
    )

    assert overrides[video_path] == 3
    assert overrides[image_path] == 8


def test_image_only_clip_duration_uses_image_duration(tmp_path):
    image_path = str(tmp_path / "stock-image-clips" / "image-clip-001.mp4")

    overrides = stock_materials.build_clip_duration_overrides(
        [image_path],
        material_type=MaterialType.image,
        video_clip_duration=3,
        image_duration=10,
    )

    assert overrides == {image_path: 10}


def test_video_only_mode_does_not_change_legacy_clip_duration_behavior(tmp_path):
    video_path = str(tmp_path / "stock-video.mp4")

    assert stock_materials.build_clip_duration_overrides(
        [video_path],
        material_type=MaterialType.video,
        video_clip_duration=3,
        image_duration=10,
    ) == {}


def test_core_combiner_accepts_per_material_clip_duration_overrides():
    assert "clip_duration_overrides" in signature(video_service.combine_videos).parameters


def test_task_finalizer_applies_material_timeline_policy():
    source = Path("app/services/task.py").read_text(encoding="utf-8")

    assert "effective_concat_mode(" in source
    assert "build_clip_duration_overrides(" in source
    assert "clip_duration_overrides=clip_duration_overrides" in source
