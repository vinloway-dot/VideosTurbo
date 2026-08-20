from pathlib import Path
from unittest.mock import patch

from app.services.music_batch import gpu


def test_windows_music_batch_temp_audio_directories_are_isolated_and_cleaned_up():
    original = {"temp_audiofile_path": "shared-temp", "logger": None}

    with patch.object(gpu.sys, "platform", "win32"):
        with gpu._isolated_moviepy_temp_audio_kwargs(original) as first_kwargs:
            first_path = Path(first_kwargs["temp_audiofile_path"])
            assert first_path.is_dir()
            assert first_kwargs["logger"] is None

            with gpu._isolated_moviepy_temp_audio_kwargs(original) as second_kwargs:
                second_path = Path(second_kwargs["temp_audiofile_path"])
                assert second_path.is_dir()
                assert second_path != first_path

            assert not second_path.exists()

        assert not first_path.exists()

    assert original["temp_audiofile_path"] == "shared-temp"


def test_non_windows_music_batch_keeps_existing_temp_audio_directory():
    original = {"temp_audiofile_path": "/tmp/task-a", "logger": None}

    with patch.object(gpu.sys, "platform", "linux"):
        with gpu._isolated_moviepy_temp_audio_kwargs(original) as effective:
            assert effective["temp_audiofile_path"] == "/tmp/task-a"
