import os

import pytest

from app.services.cloud_agent import storage as storage_module
from app.services.cloud_agent import errors as errors_module
from app.services.cloud_agent.errors import MediaValidationError
from app.services.cloud_agent.storage import CloudJobStorage


def test_prepare_creates_expected_directories_without_placeholder_media(tmp_path):
    paths = CloudJobStorage(tmp_path).prepare("job-123")

    assert paths.job_dir == tmp_path / "job-123"
    assert paths.input_dir == tmp_path / "job-123" / "input"
    assert paths.audio_dir == tmp_path / "job-123" / "audio"
    assert paths.flow_dir == tmp_path / "job-123" / "flow"
    assert paths.flow_downloads_dir == paths.flow_dir / "downloads"
    assert paths.flow_staging_dir == paths.flow_dir / "staging"
    assert paths.flow_quarantine_dir == paths.flow_dir / "quarantine"
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
    assert paths.flow_archive_file == paths.flow_downloads_dir / "product_clips.zip"
    assert paths.final_file == paths.final_dir / "final.mp4"

    assert paths.input_dir.is_dir()
    assert paths.audio_dir.is_dir()
    assert paths.flow_dir.is_dir()
    assert paths.flow_downloads_dir.is_dir()
    assert paths.flow_staging_dir.is_dir()
    assert paths.flow_quarantine_dir.is_dir()
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


def test_read_master_prompt_returns_stripped_saved_job_prompt(tmp_path):
    storage = CloudJobStorage(tmp_path)
    storage.write_inputs(
        "job-123", script="script", master_prompt="  Full Master Prompt ✓\n"
    )

    assert storage.read_master_prompt("job-123") == "Full Master Prompt ✓"


@pytest.mark.parametrize(
    ("prepared", "expected_message"),
    [(False, "unavailable"), (True, "empty")],
)
def test_read_master_prompt_rejects_unavailable_or_empty_job_prompt(
    tmp_path, prepared, expected_message
):
    storage = CloudJobStorage(tmp_path)
    if prepared:
        storage.write_inputs("job-123", script="script", master_prompt="  \n")

    with pytest.raises(ValueError, match=expected_message):
        storage.read_master_prompt("job-123")


def test_read_master_prompt_rejects_symlink_outside_the_job(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-123")
    outside_prompt = tmp_path / "outside-master-prompt.txt"
    outside_prompt.write_text("outside prompt", encoding="utf-8")
    try:
        paths.master_prompt_file.symlink_to(outside_prompt)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ValueError, match="unavailable"):
        storage.read_master_prompt("job-123")

    assert outside_prompt.read_text(encoding="utf-8") == "outside prompt"


def test_read_master_prompt_rejects_job_symlink_to_another_job_in_the_root(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    target = storage.write_inputs(
        "job-b", script="script", master_prompt="job-b private prompt"
    )
    job_link = storage.root / "job-a"
    try:
        job_link.symlink_to(target.job_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ValueError, match="unavailable"):
        storage.read_master_prompt("job-a")

    assert target.master_prompt_file.read_text(encoding="utf-8") == (
        "job-b private prompt"
    )


def test_read_master_prompt_rejects_hardlinked_prompt(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.write_inputs(
        "job-123", script="script", master_prompt="private master prompt"
    )
    outside_link = tmp_path / "outside-hardlink"
    os.link(paths.master_prompt_file, outside_link)

    with pytest.raises(ValueError, match="unavailable"):
        storage.read_master_prompt("job-123")

    assert outside_link.read_text(encoding="utf-8") == "private master prompt"


def test_read_master_prompt_rejects_oversized_sparse_prompt_without_truncating(
    tmp_path,
):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-123")
    with paths.master_prompt_file.open("wb") as prompt_file:
        prompt_file.write(b"valid-looking-prefix")
        prompt_file.truncate(CloudJobStorage.MASTER_PROMPT_MAX_BYTES + 1)

    with pytest.raises(ValueError, match="unavailable"):
        storage.read_master_prompt("job-123")

    assert paths.master_prompt_file.stat().st_size == (
        CloudJobStorage.MASTER_PROMPT_MAX_BYTES + 1
    )


def test_read_master_prompt_rejects_foreign_owned_prompt(tmp_path, monkeypatch):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.write_inputs(
        "job-123", script="script", master_prompt="owner-private-prompt"
    )
    real_fstat = os.fstat

    def report_foreign_prompt_owner(file_descriptor):
        file_stat = real_fstat(file_descriptor)
        target = os.readlink(f"/proc/self/fd/{file_descriptor}")
        if target.endswith("/master_prompt.txt"):
            values = list(file_stat)
            values[4] = file_stat.st_uid + 1
            return os.stat_result(values)
        return file_stat

    monkeypatch.setattr(storage_module.os, "fstat", report_foreign_prompt_owner)

    with pytest.raises(ValueError, match="unavailable"):
        storage.read_master_prompt("job-123")

    assert paths.master_prompt_file.read_text(encoding="utf-8") == (
        "owner-private-prompt"
    )


@pytest.mark.parametrize("replaced_component", ["root", "job", "input", "prompt"])
def test_read_master_prompt_revalidates_every_named_component_after_read(
    tmp_path, replaced_component
):
    class RacingStorage(CloudJobStorage):
        def _read_master_prompt_bytes(self, prompt_fd):
            content = super()._read_master_prompt_bytes(prompt_fd)
            replacement_root = tmp_path / f"replacement-{replaced_component}"
            if replaced_component == "root":
                self.root.rename(replacement_root)
                replacement = self.root / "job-123" / "input"
                replacement.mkdir(parents=True)
                (replacement / "master_prompt.txt").write_text(
                    "replacement-root-prompt", encoding="utf-8"
                )
            elif replaced_component == "job":
                paths.job_dir.rename(replacement_root)
                replacement = paths.job_dir / "input"
                replacement.mkdir(parents=True)
                (replacement / "master_prompt.txt").write_text(
                    "replacement-job-prompt", encoding="utf-8"
                )
            elif replaced_component == "input":
                paths.input_dir.rename(replacement_root)
                paths.input_dir.mkdir()
                paths.master_prompt_file.write_text(
                    "replacement-input-prompt", encoding="utf-8"
                )
            else:
                replacement = paths.input_dir / ".replacement-prompt"
                replacement.write_text("replacement-file-prompt", encoding="utf-8")
                os.replace(replacement, paths.master_prompt_file)
            return content

    storage = RacingStorage(tmp_path / "jobs")
    paths = storage.write_inputs(
        "job-123", script="script", master_prompt="original-master-prompt"
    )

    with pytest.raises(ValueError, match="unavailable"):
        storage.read_master_prompt("job-123")


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


def test_quarantine_flow_canonical_returns_none_when_no_clips_exist(tmp_path):
    storage = CloudJobStorage(tmp_path)
    storage.prepare("job-123")

    assert storage.quarantine_flow_canonical("job-123") is None


def test_quarantine_flow_canonical_moves_only_canonical_clips(tmp_path):
    storage = CloudJobStorage(tmp_path)
    paths = storage.prepare("job-123")
    paths.flow_files[0].write_bytes(b"clip-one")
    paths.flow_files[2].write_bytes(b"clip-three")
    paths.flow_archive_file.write_bytes(b"archive")
    staged = paths.flow_staging_dir / "clip 1.mp4"
    staged.write_bytes(b"staged")
    unrelated = paths.flow_dir / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    quarantine = storage.quarantine_flow_canonical("job-123")

    assert quarantine is not None
    assert quarantine.parent == paths.flow_quarantine_dir
    assert (quarantine / "clip_01.mp4").read_bytes() == b"clip-one"
    assert (quarantine / "clip_03.mp4").read_bytes() == b"clip-three"
    assert all(not path.exists() for path in paths.flow_files)
    assert paths.flow_archive_file.read_bytes() == b"archive"
    assert staged.read_bytes() == b"staged"
    assert unrelated.read_text(encoding="utf-8") == "keep"

    paths.flow_files[0].write_bytes(b"new-clip")
    second_quarantine = storage.quarantine_flow_canonical("job-123")
    assert second_quarantine is not None
    assert second_quarantine != quarantine


def test_quarantine_flow_canonical_refuses_external_symlink_before_moving(tmp_path):
    storage = CloudJobStorage(tmp_path)
    paths = storage.prepare("job-123")
    paths.flow_files[0].write_bytes(b"safe-clip")
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"outside")

    try:
        paths.flow_files[1].symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ValueError, match="escapes"):
        storage.quarantine_flow_canonical("job-123")

    assert paths.flow_files[0].read_bytes() == b"safe-clip"
    assert outside_file.read_bytes() == b"outside"


def test_flow_workspace_and_archive_errors_are_typed_media_failures():
    workspace_error = getattr(errors_module, "FlowWorkspaceVerificationError", None)
    archive_error = getattr(errors_module, "FlowArchiveValidationError", None)

    assert workspace_error is not None
    assert archive_error is not None
    assert issubclass(workspace_error, MediaValidationError)
    assert issubclass(archive_error, MediaValidationError)
