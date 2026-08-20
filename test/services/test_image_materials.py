import random
from pathlib import Path

import pytest

from app.models.schema import ImageMotion
from app.services import image_materials


def test_normalize_image_motion_falls_back_to_random_for_unknown_value():
    assert image_materials.normalize_image_motion("not-a-mode") == ImageMotion.random


def test_random_motion_chooses_only_real_motion_effects():
    rng = random.Random(7)
    choices = {
        image_materials.choose_image_motion(ImageMotion.random, rng=rng)
        for _ in range(20)
    }

    assert choices
    assert choices <= {
        ImageMotion.slow_zoom_in,
        ImageMotion.slow_zoom_out,
        ImageMotion.pan_left_right,
        ImageMotion.pan_right_left,
    }
    assert ImageMotion.none not in choices
    assert ImageMotion.random not in choices


def test_explicit_image_motion_is_not_changed():
    for motion in (
        ImageMotion.slow_zoom_in,
        ImageMotion.slow_zoom_out,
        ImageMotion.pan_left_right,
        ImageMotion.pan_right_left,
        ImageMotion.none,
    ):
        assert image_materials.choose_image_motion(motion) == motion


def test_validate_image_duration_accepts_one_through_thirty_seconds():
    assert image_materials.validate_image_duration(1) == 1
    assert image_materials.validate_image_duration(8) == 8
    assert image_materials.validate_image_duration(30) == 30

    with pytest.raises(ValueError, match="between 1 and 30"):
        image_materials.validate_image_duration(0)
    with pytest.raises(ValueError, match="between 1 and 30"):
        image_materials.validate_image_duration(31)


def test_interpolate_motion_fraction_is_clamped():
    assert image_materials.motion_fraction(-1.0, 8.0) == 0.0
    assert image_materials.motion_fraction(4.0, 8.0) == pytest.approx(0.5)
    assert image_materials.motion_fraction(99.0, 8.0) == 1.0


def test_prepared_image_clip_name_keeps_stable_source_identity():
    source = Path("stock-image-pexels-12345.jpg")

    assert (
        image_materials.output_filename_for_image(source)
        == "image-clip-stock-image-pexels-12345.mp4"
    )


def test_prepare_stock_image_clip_reuses_existing_stable_clip_without_rerender(
    monkeypatch, tmp_path
):
    image_path = tmp_path / "stock-image-pexels-12345.jpg"
    image_path.write_bytes(b"image")
    output_dir = tmp_path / "clips"
    output_dir.mkdir()
    existing = output_dir / image_materials.output_filename_for_image(image_path)
    existing.write_bytes(b"video")

    def unexpected_build(*args, **kwargs):
        raise AssertionError("stable stock image clip should be reused")

    monkeypatch.setattr(image_materials, "_build_motion_clip", unexpected_build)

    result = image_materials.prepare_image_clips(
        [image_path],
        output_dir=output_dir,
        duration=8,
        motion="none",
        aspect="16:9",
        codec="libx264",
    )

    assert result == [str(existing)]
