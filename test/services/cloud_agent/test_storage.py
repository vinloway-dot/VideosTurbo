from pathlib import Path

import pytest

from app.services.cloud_agent import storage as storage_module
from app.services.cloud_agent.storage import CloudJobStorage


def test_prepare_creates_expected_directories_without_placeholder_media(tmp_path):
    paths = CloudJobStorage(tmp_path).prepare("job-123")

    assert paths.job_dir == tmp_path / "job-123"
    assert paths.input_dir == tmp_path / "job-123" / "input"
    assert paths.audio_dir == tmp_path / "job-123" / "audio"
    assert paths.flow_dir == tmp_path / "job-123" / "flow"
    assert paths.screenshots_dir == tmp_path / "job-123" / "screenshots"
    assert paths.logs_dir == tmp_path / "job-123" / "logs"
    assert paths.final_dir == tmp_path / "job-123" / "final"

    assert paths.script_file == paths.input_dir / "script.txt"
    assert paths.master_prompt_file == paths.input_dir / "master_prompt.txt"
    assert paths.voice_file == paths.audio_dir / "voice.mp3"
    assert [path.name for path in paths.flow_files] == [
        "clip_01.mp4",
        "clip_02.mp4",
        "clip_03.mp4",
        "clip_04.mp4",
        "clip_05.mp4",
        "clip_06.mp4",
    ]
    assert paths.final_file == paths.final_dir / "final.mp4"

    assert paths.input_dir.is_dir()
    assert paths.audio_dir.is_dir()
    assert paths.flow_dir.is_dir()
    assert paths.screenshots_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.final_dir.is_dir()
    assert not paths.voice_file.exists()
    assert not paths.final_file.exists()
    assert all(not path.exists() for path in paths.flow_files)


def test_write_inputs_writes_utf8_script_and_master_prompt(tmp_path):
    storage = CloudJobStorage(tmp_path)

    paths = storage.write_inputs(
        "job-ไทย",
        script="สคริปต์ภาษาไทย\nsecond line",
        master_prompt="Master Prompt ✓",
    )

    assert paths.script_file.read_text(encoding="utf-8") == "สคริปต์ภาษาไทย\nsecond line"
    assert paths.master_prompt_file.read_text(encoding="utf-8") == "Master Prompt ✓"


def test_default_root_reuses_repository_storage_helper(monkeypatch, tmp_path):
    expected_root = tmp_path / "repo-storage" / "jobs"
    calls = []

    def fake_storage_dir(sub_dir="", create=False):
        calls.append((sub_dir, create))
        return str(expected_root)

    monkeypatch.setattr(storage_module.utils, "storage_dir", fake_storage_dir)

    storage = CloudJobStorage()

    assert storage.root == expected_root
    assert calls == [("jobs", True)]


@pytest.mark.parametrize(
    "job_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "nested/job",
        "nested\\job",
        "/absolute/job",
        "C:\\absolute\\job",
    ],
)
def test_job_id_rejects_paths_and_directory_traversal(tmp_path, job_id):
    with pytest.raises(ValueError):
        CloudJobStorage(tmp_path).prepare(job_id)


def test_cleanup_removes_only_flow_source_files(tmp_path):
    storage = CloudJobStorage(tmp_path)
    paths = storage.prepare("job-123")
    for flow_file in paths.flow_files:
        flow_file.write_bytes(b"flow")
    paths.voice_file.write_bytes(b"voice")
    paths.final_file.write_bytes(b"final")
    outside_file = tmp_path / "keep-me.mp4"
    outside_file.write_bytes(b"outside")

    storage.cleanup_flow_sources("job-123")

    assert all(not path.exists() for path in paths.flow_files)
    assert paths.voice_file.read_bytes() == b"voice"
    assert paths.final_file.read_bytes() == b"final"
    assert outside_file.read_bytes() == b"outside"


def test_cleanup_refuses_flow_symlink_that_resolves_outside_job(tmp_path):
    storage = CloudJobStorage(tmp_path)
    paths = storage.prepare("job-123")
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"outside")

    try:
        paths.flow_files[0].symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ValueError):
        storage.cleanup_flow_sources("job-123")

    assert outside_file.read_bytes() == b"outside"
