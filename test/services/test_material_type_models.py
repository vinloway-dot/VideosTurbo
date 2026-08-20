import pytest
from pydantic import ValidationError

from app.models.schema import ImageMotion, MaterialType, VideoParams


def _params(**updates):
    values = {"video_subject": "test subject"}
    values.update(updates)
    return VideoParams(**values)


def test_video_params_default_to_legacy_video_mode():
    params = _params()

    assert params.material_type == MaterialType.video
    assert params.image_duration == 8
    assert params.image_motion == ImageMotion.random
    assert params.video_source == "pexels"


def test_online_image_and_mixed_modes_route_to_internal_source_variants():
    assert _params(video_source="pexels", material_type="image").video_source == "pexels_image"
    assert _params(video_source="pixabay", material_type="mixed").video_source == "pixabay_mixed"


def test_video_mode_keeps_existing_online_source_unchanged():
    assert _params(video_source="pexels", material_type="video").video_source == "pexels"
    assert _params(video_source="coverr", material_type="video").video_source == "coverr"


def test_unsupported_online_material_type_is_rejected():
    with pytest.raises(ValidationError, match="does not support material type"):
        _params(video_source="coverr", material_type="image")

    with pytest.raises(ValidationError, match="does not support material type"):
        _params(video_source="loomloom", material_type="mixed")


def test_local_material_type_remains_explicit_without_source_rewrite():
    params = _params(video_source="local", material_type="image")

    assert params.video_source == "local"
    assert params.material_type == MaterialType.image


def test_image_duration_is_limited_to_one_through_thirty_seconds():
    with pytest.raises(ValidationError):
        _params(image_duration=0)
    with pytest.raises(ValidationError):
        _params(image_duration=31)


def test_image_motion_accepts_all_approved_modes():
    expected = {
        ImageMotion.slow_zoom_in,
        ImageMotion.slow_zoom_out,
        ImageMotion.pan_left_right,
        ImageMotion.pan_right_left,
        ImageMotion.random,
        ImageMotion.none,
    }

    actual = {
        _params(image_motion=value.value).image_motion
        for value in expected
    }
    assert actual == expected
