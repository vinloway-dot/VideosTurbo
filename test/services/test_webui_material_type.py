from pathlib import Path


def test_main_video_settings_expose_material_type_and_ken_burns_controls():
    source = Path("webui/Main.py").read_text(encoding="utf-8")

    assert '"Material Type"' in source
    assert '"Video + Image"' in source
    assert '"Image Duration (seconds)"' in source
    assert '"Ken Burns Effect"' in source
    assert '"Slow Zoom In"' in source
    assert '"Pan Left → Right"' in source
    assert 'f"{selected_video_source}_{params.material_type}"' in source


def test_music_batch_exposes_global_and_override_material_controls():
    source = Path("webui/music_batch.py").read_text(encoding="utf-8")

    assert '"Material Type"' in source
    assert '"Material Type override"' in source
    assert '"Image Duration (seconds)"' in source
    assert '"Image Duration override (seconds)"' in source
    assert '"Ken Burns Effect"' in source
    assert '"Ken Burns Effect override"' in source
    assert 'material_type=global_material_type' in source
    assert 'image_duration=int(global_image_duration)' in source
    assert 'image_motion=global_image_motion' in source
