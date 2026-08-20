from pathlib import Path

from app.models.schema import MaterialType, VideoAspect, VideoConcatMode
from app.services import stock_materials


def test_supported_material_types_are_provider_specific():
    assert stock_materials.supported_material_types("pexels") == {
        MaterialType.video,
        MaterialType.image,
        MaterialType.mixed,
    }
    assert stock_materials.supported_material_types("pixabay") == {
        MaterialType.video,
        MaterialType.image,
        MaterialType.mixed,
    }
    assert stock_materials.supported_material_types("coverr") == {MaterialType.video}
    assert stock_materials.supported_material_types("loomloom") == {MaterialType.video}
    assert stock_materials.supported_material_types("local") == {
        MaterialType.video,
        MaterialType.image,
        MaterialType.mixed,
    }


def test_interleave_material_paths_alternates_then_uses_remaining_items():
    assert stock_materials.interleave_material_paths(
        ["v1.mp4", "v2.mp4"],
        ["i1.mp4", "i2.mp4", "i3.mp4"],
    ) == ["v1.mp4", "i1.mp4", "v2.mp4", "i2.mp4", "i3.mp4"]


def test_download_mixed_materials_uses_both_sources_when_available(monkeypatch, tmp_path):
    calls = []

    def fake_download_videos(**kwargs):
        calls.append(("video", kwargs["audio_duration"]))
        return ["video-a.mp4", "video-b.mp4"]

    def fake_download_images(**kwargs):
        calls.append(("image", kwargs["audio_duration"]))
        return [tmp_path / "image-a.jpg", tmp_path / "image-b.jpg"]

    def fake_prepare_image_clips(paths, **kwargs):
        assert [Path(path).name for path in paths] == ["image-a.jpg", "image-b.jpg"]
        return ["image-a.mp4", "image-b.mp4"]

    monkeypatch.setattr(stock_materials.material, "download_videos", fake_download_videos)
    monkeypatch.setattr(stock_materials.stock_images, "download_images", fake_download_images)
    monkeypatch.setattr(
        stock_materials.image_materials,
        "prepare_image_clips",
        fake_prepare_image_clips,
    )

    paths = stock_materials.download_stock_materials(
        task_id="task-1",
        search_terms=["ocean"],
        source="pexels",
        material_type=MaterialType.mixed,
        video_aspect=VideoAspect.landscape,
        video_concat_mode=VideoConcatMode.sequential,
        audio_duration=60.0,
        max_clip_duration=8,
        image_duration=8,
        image_motion="random",
        match_script_order=False,
    )

    assert paths == ["video-a.mp4", "image-a.mp4", "video-b.mp4", "image-b.mp4"]
    assert calls == [("video", 30.0), ("image", 30.0)]


def test_download_mixed_materials_falls_back_to_images_when_video_search_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(stock_materials.material, "download_videos", lambda **kwargs: [])
    monkeypatch.setattr(
        stock_materials.stock_images,
        "download_images",
        lambda **kwargs: [tmp_path / "image-a.jpg"],
    )
    monkeypatch.setattr(
        stock_materials.image_materials,
        "prepare_image_clips",
        lambda paths, **kwargs: ["image-a.mp4"],
    )

    paths = stock_materials.download_stock_materials(
        task_id="task-2",
        search_terms=["forest"],
        source="pixabay",
        material_type="mixed",
        video_aspect="16:9",
        video_concat_mode="random",
        audio_duration=30.0,
        max_clip_duration=8,
        image_duration=8,
        image_motion="none",
        match_script_order=False,
    )

    assert paths == ["image-a.mp4"]
