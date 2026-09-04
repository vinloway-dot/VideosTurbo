from app.models.schema import ImageMotion, MaterialType
from app.services.music_batch.manager import MusicBatchManager
from app.services.music_batch.models import BatchSettings, SongItem, resolve_song_settings


def test_render_params_include_resolved_material_settings(tmp_path):
    captured = {}

    def adapter(params, output_path):
        captured.update(params)
        output_path.write_bytes(b"video")
        return output_path

    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    song = SongItem(source_path=str(audio), added_index=0)
    settings = BatchSettings(
        output_root=str(tmp_path),
        material_type="mixed",
        image_duration=12,
        image_motion="pan_right_left",
    )

    MusicBatchManager(render_adapter=adapter).render_song(
        song,
        resolve_song_settings(settings, song),
        tmp_path / "song.mp4",
    )

    assert captured["material_type"] == MaterialType.mixed
    assert captured["image_duration"] == 12
    assert captured["image_motion"] == ImageMotion.pan_right_left
