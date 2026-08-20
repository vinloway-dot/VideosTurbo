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


def test_mixed_duration_overrides_only_mark_images(tmp_path):
    video_path = str(tmp_path / "stock-video.mp4")
    image_path = str(tmp_path / "stock-image-clips" / "image-clip-001.mp4")

    overrides = stock_materials.build_clip_duration_overrides(
        [video_path, image_path],
        material_type=MaterialType.mixed,
        video_clip_duration=3,
        image_duration=8,
    )

    # Stock videos stay on the legacy max_clip_duration + clip_speed path. Prepared
    # image clips are marked explicitly so Image Duration remains their final display
    # duration and is not changed by the video's Clip Speed setting.
    assert video_path not in overrides
    assert overrides == {image_path: 8}


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


def test_core_combiner_keeps_speed_off_explicit_duration_overrides():
    source = Path("app/services/video.py").read_text(encoding="utf-8")

    assert "duration_overridden =" in source
    assert "if normalized_clip_speed != 1.0 and not duration_overridden:" in source


def test_task_finalizer_applies_material_timeline_policy():
    source = Path("app/services/task.py").read_text(encoding="utf-8")

    assert "effective_concat_mode(" in source
    assert "build_clip_duration_overrides(" in source
    assert "clip_duration_overrides=clip_duration_overrides" in source
