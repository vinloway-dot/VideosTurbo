from pathlib import Path


def test_music_batch_retains_its_independent_material_controls():
    source = Path("webui/music_batch.py").read_text(encoding="utf-8")

    for label in (
        '"Material Type"',
        '"Material Type override"',
        '"Image Duration (seconds)"',
        '"Image Duration override (seconds)"',
        '"Ken Burns Effect"',
        '"Ken Burns Effect override"',
    ):
        assert label in source
