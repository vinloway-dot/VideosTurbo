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


def test_main_task_restore_normalizes_synthetic_material_source_and_restores_image_controls():
    source = Path("webui/Main.py").read_text(encoding="utf-8")

    assert "base_video_source = stock_materials.base_source(video_source)" in source
    assert (
        'restored_material_type = str(params.get("material_type") or "video")'
        in source
    )
    assert '_set_stable_widget_value("video_source_select", base_video_source)' in source
    assert 'f"material_type_for_{base_video_source}"' in source
    assert 'f"image_duration_for_{base_video_source}"' in source
    assert 'f"image_motion_for_{base_video_source}"' in source
    # Aspect widgets are keyed by the effective synthetic source after material-type
    # selection (for example pexels_image), so task restore must keep that key.
    assert 'f"video_aspect_for_{video_source}"' in source


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
