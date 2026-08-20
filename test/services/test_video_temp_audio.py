from pathlib import Path
from unittest.mock import patch

from app.services import video as vd


def test_windows_temp_audio_directories_are_isolated_and_cleaned_up():
    with patch.object(vd.sys, "platform", "win32"):
        with vd._isolated_temp_audio_dir("unused-output-dir") as first:
            first_path = Path(first)
            assert first_path.is_dir()

            with vd._isolated_temp_audio_dir("unused-output-dir") as second:
                second_path = Path(second)
                assert second_path.is_dir()
                assert second_path != first_path

            assert not second_path.exists()

        assert not first_path.exists()


def test_non_windows_temp_audio_directory_keeps_existing_output_dir(tmp_path):
    with patch.object(vd.sys, "platform", "linux"):
        with vd._isolated_temp_audio_dir(str(tmp_path)) as temp_audio_dir:
            assert temp_audio_dir == str(tmp_path)
