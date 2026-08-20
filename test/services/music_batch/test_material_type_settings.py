from app.models.schema import ImageMotion, MaterialType
from app.services.music_batch.models import (
    BatchSettings,
    SongItem,
    SongOverride,
    resolve_song_settings,
)


def test_music_batch_material_defaults_keep_legacy_video_behavior():
    settings = BatchSettings(output_root="D:/out")

    assert settings.material_type == MaterialType.video
    assert settings.image_duration == 8
    assert settings.image_motion == ImageMotion.random


def test_music_batch_song_can_override_material_type_and_image_settings():
    settings = BatchSettings(
        output_root="D:/out",
        material_type="video",
        image_duration=8,
        image_motion="random",
    )
    song = SongItem(
        source_path="D:/music/a.mp3",
        added_index=0,
        override=SongOverride(
            material_type="mixed",
            image_duration=12,
            image_motion="pan_left_right",
        ),
    )

    resolved = resolve_song_settings(settings, song)

    assert resolved["material_type"] == MaterialType.mixed
    assert resolved["image_duration"] == 12
    assert resolved["image_motion"] == ImageMotion.pan_left_right
