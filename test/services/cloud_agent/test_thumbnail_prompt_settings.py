import fcntl
import os
import stat
from dataclasses import FrozenInstanceError
import multiprocessing
from pathlib import Path
import threading

import pytest
import toml

from app.config import config
from app.services.cloud_agent import factory
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)
from app.services.cloud_agent.thumbnail_prompt.models import (
    ThumbnailPromptSettingsPayload,
)


_CORRUPT_SETTINGS_MESSAGE = (
    "Saved Thumbnail Prompt settings are corrupt. "
    "Save Thumbnail Prompt Settings to repair them."
)


def _write_provider_key(
    settings_path,
    provider_id,
    value,
    started,
    done,
    save_entered=None,
    release_save=None,
    results=None,
):
    try:
        service = ThumbnailPromptSettingsService(settings_path=settings_path)
        if save_entered is not None and release_save is not None:
            original_save = service._save_locked

            def paused_save(*args, **kwargs):
                save_entered.set()
                if not release_save.wait(timeout=10):
                    raise RuntimeError("timed out waiting to release settings save")
                return original_save(*args, **kwargs)

            service._save_locked = paused_save
        started.set()
        service.set_api_key(provider_id, value)
        if results is not None:
            results.put((provider_id, "ok"))
    except ThumbnailPromptError as exc:
        if results is not None:
            results.put((provider_id, exc.code))
        else:
            raise
    finally:
        done.set()


def _valid_payload(**overrides):
    values = {
        "master_prompt": "Create a striking thumbnail.",
        "default_provider": "aihubmix",
        "aihubmix_model": "gpt-5.6-sol",
        "aihubmix_custom_model_id": "",
        "aihubmix_base_url": "https://aihubmix.example/v1",
        "openrouter_model": "openai/gpt-5.6-sol",
        "openrouter_custom_model_id": "",
        "openrouter_base_url": "https://openrouter.example/api/v1",
    }
    values.update(overrides)
    return ThumbnailPromptSettingsPayload(**values)


@pytest.mark.parametrize(
    ("persisted", "secret_marker"),
    [
        (b"[malformed", ""),
        (b'master_prompt = "\xff\xfe"\n', ""),
        (
            b'aihubmix_custom_model = ["wrong-type-secret-marker"]\n',
            "wrong-type-secret-marker",
        ),
        (
            toml.dumps(
                {"master_prompt": "oversized-secret-marker-" + "x" * 8000}
            ).encode(),
            "oversized-secret-marker",
        ),
        (
            toml.dumps(
                {"aihubmix_api_key": "oversized-key-marker-" + "x" * 4097}
            ).encode(),
            "oversized-key-marker",
        ),
    ],
)
def test_corrupt_settings_reads_return_sanitized_recovery_state(
    tmp_path, persisted, secret_marker
):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    settings_path.write_bytes(persisted)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    settings = service.get_settings()
    providers = service.list_providers()

    assert settings.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert settings.default_provider is None
    assert settings.master_prompt == ""
    assert all(provider.api_key_configured is False for provider in providers)
    assert all(
        provider.configuration_error == _CORRUPT_SETTINGS_MESSAGE
        for provider in providers
    )
    serialized = settings.model_dump_json() + "".join(
        provider.model_dump_json() for provider in providers
    )
    if secret_marker:
        assert secret_marker not in serialized
    assert "validation" not in serialized.lower()


def test_settings_read_os_error_returns_sanitized_recovery_state(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    settings_path.write_text('master_prompt = "must-not-leak"\n')
    settings_path.chmod(0)
    try:
        settings = ThumbnailPromptSettingsService(
            settings_path=settings_path
        ).get_settings()
    finally:
        settings_path.chmod(0o600)

    assert settings.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert "must-not-leak" not in settings.model_dump_json()


def test_successful_settings_repair_preserves_corrupt_file(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    corrupt_bytes = b'[broken = "corrupt-secret-marker"'
    settings_path.write_bytes(corrupt_bytes)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    updated = service.update_settings(_valid_payload())

    assert updated.configuration_error is None
    assert toml.load(settings_path)["master_prompt"] == "Create a striking thumbnail."
    preserved = list(settings_path.parent.glob(".corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt_bytes


def test_successful_api_key_repair_preserves_corrupt_file(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    corrupt_bytes = b"\xffcorrupt-key-file"
    settings_path.write_bytes(corrupt_bytes)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    provider = service.set_api_key("openrouter", "repaired-key")

    assert provider.api_key_configured is True
    assert toml.load(settings_path)["openrouter_api_key"] == "repaired-key"
    preserved = list(settings_path.parent.glob(".corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt_bytes


def test_corrupt_backup_is_private_and_preserves_secret_bytes(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    corrupt_bytes = b'[broken = "corrupt-secret-sentinel"'
    settings_path.write_bytes(corrupt_bytes)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    updated = service.update_settings(_valid_payload())

    preserved = list(settings_path.parent.glob(".corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt_bytes
    assert stat.S_IMODE(preserved[0].stat().st_mode) == 0o600
    assert "corrupt-secret-sentinel" not in updated.model_dump_json()


def test_repeated_failed_atomic_repairs_do_not_accumulate_corrupt_backups(
    tmp_path, monkeypatch
):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    corrupt_bytes = b'[broken = "canonical-secret-sentinel"'
    settings_path.write_bytes(corrupt_bytes)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    def fail_replace(*args, **kwargs):
        raise OSError("injected replace failure")

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.replace", fail_replace
    )

    for _ in range(3):
        with pytest.raises(OSError, match="injected replace failure"):
            service.update_settings(_valid_payload())

    assert settings_path.read_bytes() == corrupt_bytes
    recovered = service.get_settings()
    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert "canonical-secret-sentinel" not in recovered.model_dump_json()
    assert list(settings_path.parent.glob(".corrupt-*")) == []
    assert list(settings_path.parent.glob(".settings.toml.*.tmp")) == []


def test_hard_linked_settings_file_is_unsafe_and_cannot_be_repaired(tmp_path):
    settings_dir = tmp_path / "thumbnail_prompt"
    settings_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside-settings.toml"
    outside_bytes = b'[broken = "hard-link-secret-sentinel"'
    outside.write_bytes(outside_bytes)
    settings_path = settings_dir / "settings.toml"
    os.link(outside, settings_path)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    recovered = service.get_settings()
    with pytest.raises(ThumbnailPromptError) as error:
        service.update_settings(_valid_payload())

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert "hard-link-secret-sentinel" not in recovered.model_dump_json()
    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert outside.read_bytes() == outside_bytes
    assert settings_path.read_bytes() == outside_bytes
    assert list(settings_dir.glob(".corrupt-*")) == []


def test_non_private_canonical_mode_is_unsafe_and_cannot_be_updated(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    persisted = b'aihubmix_api_key = "mode-secret-sentinel"\n'
    settings_path.write_bytes(persisted)
    settings_path.chmod(0o644)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    recovered = service.get_settings()
    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("openrouter", "must-not-be-written")

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert "mode-secret-sentinel" not in recovered.model_dump_json()
    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert settings_path.read_bytes() == persisted


def test_canonical_substitution_during_read_returns_sanitized_unsafe_state(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    settings_path.write_bytes(b'master_prompt = "original-safe-prompt"\n')
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    real_read = service._read_bounded
    attacker_bytes = b'master_prompt = "attacker-read-secret"\n'

    def substitute_after_read(file_descriptor):
        content = real_read(file_descriptor)
        attacker_path = settings_path.parent / ".attacker-canonical"
        attacker_path.write_bytes(attacker_bytes)
        attacker_path.chmod(0o644)
        os.replace(attacker_path, settings_path)
        return content

    service._read_bounded = substitute_after_read

    recovered = service.get_settings()

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert "original-safe-prompt" not in recovered.model_dump_json()
    assert "attacker-read-secret" not in recovered.model_dump_json()
    assert settings_path.read_bytes() == attacker_bytes


def test_foreign_owned_canonical_is_unsafe_and_secret_redacted(tmp_path, monkeypatch):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    persisted = b'aihubmix_api_key = "owner-secret-sentinel"\n'
    settings_path.write_bytes(persisted)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    real_fstat = os.fstat

    def report_foreign_owner(file_descriptor):
        file_stat = real_fstat(file_descriptor)
        target = os.readlink(f"/proc/self/fd/{file_descriptor}")
        if target.endswith("/settings.toml"):
            values = list(file_stat)
            values[4] = file_stat.st_uid + 1
            return os.stat_result(values)
        return file_stat

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.fstat",
        report_foreign_owner,
    )

    recovered = service.get_settings()
    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("openrouter", "must-not-be-written")

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert "owner-secret-sentinel" not in recovered.model_dump_json()
    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert settings_path.read_bytes() == persisted


def test_oversized_sparse_corrupt_file_is_not_copied_or_repaired(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    with settings_path.open("wb") as settings_file:
        settings_file.truncate(16 * 1024 * 1024)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    recovered = service.get_settings()
    with pytest.raises(ThumbnailPromptError) as error:
        service.update_settings(_valid_payload())

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert settings_path.stat().st_size == 16 * 1024 * 1024
    assert list(settings_path.parent.glob(".corrupt-*")) == []


def test_temp_substitution_is_rejected_and_attacker_file_is_not_deleted(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    real_verify_lock = service._verify_lock_identity_locked
    attacker_bytes = b'aihubmix_api_key = "attacker-temp-secret"\n'
    substituted = False

    def substitute_temp_after_lock_check(directory_fd, lock_fd):
        nonlocal substituted
        real_verify_lock(directory_fd, lock_fd)
        temporary_names = [
            name
            for name in os.listdir(directory_fd)
            if name.startswith(".settings.toml.") and name.endswith(".tmp")
        ]
        if temporary_names and not substituted:
            substituted = True
            attacker_path = settings_path.parent / ".attacker-temp"
            attacker_path.write_bytes(attacker_bytes)
            attacker_path.chmod(0o644)
            os.replace(attacker_path, settings_path.parent / temporary_names[0])

    service._verify_lock_identity_locked = substitute_temp_after_lock_check

    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("aihubmix", "legitimate-secret")

    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert not settings_path.exists()
    remaining_temps = list(settings_path.parent.glob(".settings.toml.*.tmp"))
    assert len(remaining_temps) == 1
    assert remaining_temps[0].read_bytes() == attacker_bytes
    assert stat.S_IMODE(remaining_temps[0].stat().st_mode) == 0o644


@pytest.mark.parametrize(
    "failure_mode",
    ["partial-write", "full-write-then-raise", "fsync"],
)
def test_failed_temp_persistence_removes_created_temp_and_submitted_secret(
    tmp_path, monkeypatch, failure_mode
):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    submitted_secret = "submitted-temp-secret-sentinel"
    real_write = os.write
    real_fsync = os.fsync
    temp_write_calls = 0

    def is_temporary_descriptor(file_descriptor):
        target = os.readlink(f"/proc/self/fd/{file_descriptor}")
        return target.endswith(".tmp") and "/.settings.toml." in target

    def fail_temp_write(file_descriptor, content):
        nonlocal temp_write_calls
        if not is_temporary_descriptor(file_descriptor) or failure_mode == "fsync":
            return real_write(file_descriptor, content)
        temp_write_calls += 1
        if failure_mode == "partial-write" and temp_write_calls == 1:
            return real_write(file_descriptor, content[:-1])
        if failure_mode == "full-write-then-raise":
            real_write(file_descriptor, content)
        raise OSError(f"injected {failure_mode} failure")

    def fail_temp_fsync(file_descriptor):
        if failure_mode == "fsync" and is_temporary_descriptor(file_descriptor):
            raise OSError("injected fsync failure")
        return real_fsync(file_descriptor)

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.write",
        fail_temp_write,
    )
    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.fsync",
        fail_temp_fsync,
    )

    with pytest.raises(OSError, match=f"injected {failure_mode} failure"):
        service.set_api_key("aihubmix", submitted_secret)

    assert not settings_path.exists()
    assert list(settings_path.parent.glob(".settings.toml.*.tmp")) == []
    persisted_directory_bytes = b"".join(
        entry.read_bytes()
        for entry in settings_path.parent.iterdir()
        if entry.is_file()
    )
    assert submitted_secret.encode() not in persisted_directory_bytes


def test_post_replace_canonical_mode_is_verified_before_success(tmp_path, monkeypatch):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    real_replace = os.replace

    def replace_then_weaken_mode(source, destination, **kwargs):
        real_replace(source, destination, **kwargs)
        os.chmod(destination, 0o644, dir_fd=kwargs["dst_dir_fd"])

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.replace",
        replace_then_weaken_mode,
    )

    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("aihubmix", "legitimate-secret")

    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o644


def test_failed_validation_does_not_overwrite_or_preserve_corrupt_file(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    corrupt_bytes = b"[invalid"
    settings_path.write_bytes(corrupt_bytes)
    settings_path.chmod(0o600)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    with pytest.raises(ThumbnailPromptError) as error:
        service.update_settings(_valid_payload(aihubmix_base_url="not-a-url"))

    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
    assert settings_path.read_bytes() == corrupt_bytes
    assert list(settings_path.parent.glob(".corrupt-*")) == []


def test_concurrent_process_api_key_updates_both_survive(tmp_path):
    context = multiprocessing.get_context("fork")
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    first_started = context.Event()
    first_done = context.Event()
    first_save_entered = context.Event()
    release_first_save = context.Event()
    second_started = context.Event()
    second_done = context.Event()
    first = context.Process(
        target=_write_provider_key,
        args=(
            settings_path,
            "aihubmix",
            "first-process-key",
            first_started,
            first_done,
            first_save_entered,
            release_first_save,
        ),
    )
    second = context.Process(
        target=_write_provider_key,
        args=(
            settings_path,
            "openrouter",
            "second-process-key",
            second_started,
            second_done,
        ),
    )

    started_processes = []
    try:
        first.start()
        started_processes.append(first)
        assert first_started.wait(timeout=5)
        assert first_save_entered.wait(timeout=5)
        second.start()
        started_processes.append(second)
        assert second_started.wait(timeout=5)
        second_finished_while_first_save_was_paused = second_done.wait(timeout=1)
        release_first_save.set()
        first.join(timeout=10)
        second.join(timeout=10)

        assert first.exitcode == 0
        assert second.exitcode == 0
        assert second_finished_while_first_save_was_paused is False
        service = ThumbnailPromptSettingsService(settings_path=settings_path)
        assert service.get_provider("aihubmix").api_key_configured is True
        assert service.get_provider("openrouter").api_key_configured is True
    finally:
        release_first_save.set()
        for process in started_processes:
            if process.is_alive():
                process.terminate()
        for process in started_processes:
            process.join(timeout=5)


def test_replaced_lock_inode_aborts_stale_writer_without_lost_update(tmp_path):
    context = multiprocessing.get_context("fork")
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    first_started = context.Event()
    first_done = context.Event()
    first_save_entered = context.Event()
    release_first_save = context.Event()
    second_started = context.Event()
    second_done = context.Event()
    results = context.Queue()
    first = context.Process(
        target=_write_provider_key,
        args=(
            settings_path,
            "aihubmix",
            "stale-writer-key",
            first_started,
            first_done,
            first_save_entered,
            release_first_save,
            results,
        ),
    )
    second = context.Process(
        target=_write_provider_key,
        args=(
            settings_path,
            "openrouter",
            "replacement-lock-key",
            second_started,
            second_done,
            None,
            None,
            results,
        ),
    )
    started_processes = []

    try:
        first.start()
        started_processes.append(first)
        assert first_started.wait(timeout=5)
        assert first_save_entered.wait(timeout=5)
        lock_path = settings_path.parent / ".settings.lock"
        lock_path.unlink()
        recreated_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(recreated_fd)

        second.start()
        started_processes.append(second)
        assert second_started.wait(timeout=5)
        assert second_done.wait(timeout=5)
        release_first_save.set()
        assert first_done.wait(timeout=5)
        first.join(timeout=10)
        second.join(timeout=10)

        assert first.exitcode == 0
        assert second.exitcode == 0
        outcomes = {results.get(timeout=2) for _ in range(2)}
        assert outcomes == {
            ("aihubmix", "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"),
            ("openrouter", "ok"),
        }
        service = ThumbnailPromptSettingsService(settings_path=settings_path)
        assert service.get_provider("aihubmix").api_key_configured is False
        assert service.get_provider("openrouter").api_key_configured is True
    finally:
        release_first_save.set()
        for process in started_processes:
            if process.is_alive():
                process.terminate()
        for process in started_processes:
            process.join(timeout=5)


def test_symlinked_settings_parent_is_rejected_without_outside_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "thumbnail_prompt").symlink_to(outside, target_is_directory=True)
    service = ThumbnailPromptSettingsService(
        settings_path=storage / "thumbnail_prompt" / "settings.toml"
    )

    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("aihubmix", "must-not-be-written")

    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert list(outside.iterdir()) == []


def test_symlinked_settings_file_is_not_read_or_written(tmp_path):
    settings_dir = tmp_path / "thumbnail_prompt"
    settings_dir.mkdir(mode=0o700)
    outside_file = tmp_path / "outside-settings.toml"
    outside_bytes = b'master_prompt = "outside-secret-marker"\n'
    outside_file.write_bytes(outside_bytes)
    settings_path = settings_dir / "settings.toml"
    settings_path.symlink_to(outside_file)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    settings = service.get_settings()
    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("aihubmix", "must-not-be-written")

    assert settings.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert "outside-secret-marker" not in settings.model_dump_json()
    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert outside_file.read_bytes() == outside_bytes
    assert settings_path.is_symlink()


def test_symlinked_lock_file_is_rejected_without_outside_write(tmp_path):
    settings_dir = tmp_path / "thumbnail_prompt"
    settings_dir.mkdir(mode=0o700)
    outside_lock = tmp_path / "outside-lock"
    outside_lock.write_bytes(b"outside-lock-sentinel")
    (settings_dir / ".settings.lock").symlink_to(outside_lock)
    settings_path = settings_dir / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("openrouter", "must-not-be-written")

    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert outside_lock.read_bytes() == b"outside-lock-sentinel"
    assert not settings_path.exists()


def test_lock_fstat_failure_does_not_leak_open_descriptors(tmp_path, monkeypatch):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    descriptor_count_before = len(os.listdir("/proc/self/fd"))
    real_fstat = os.fstat

    def fail_lock_fstat(file_descriptor):
        target = os.readlink(f"/proc/self/fd/{file_descriptor}")
        if target.endswith("/.settings.lock"):
            raise OSError("injected lock fstat failure")
        return real_fstat(file_descriptor)

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.fstat",
        fail_lock_fstat,
    )

    recovered = service.get_settings()

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert len(os.listdir("/proc/self/fd")) == descriptor_count_before


def test_ancestor_close_failure_closes_newly_opened_descriptor(tmp_path, monkeypatch):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    real_open_directory = service._open_or_create_directory_locked
    real_close = os.close
    opened_descriptors = []
    close_failed = False

    def record_opened_directory(parent_fd, component, *, allow_create):
        opened_fd = real_open_directory(parent_fd, component, allow_create=allow_create)
        opened_descriptors.append(opened_fd)
        return opened_fd

    def close_then_report_failure(file_descriptor):
        nonlocal close_failed
        if (
            not close_failed
            and opened_descriptors
            and file_descriptor != opened_descriptors[-1]
        ):
            close_failed = True
            real_close(file_descriptor)
            raise OSError("injected ancestor close failure")
        return real_close(file_descriptor)

    service._open_or_create_directory_locked = record_opened_directory
    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.close",
        close_then_report_failure,
    )

    try:
        with pytest.raises(OSError, match="injected ancestor close failure"):
            service._open_directory_handles()

        assert close_failed is True
        for file_descriptor in opened_descriptors:
            with pytest.raises(OSError):
                os.fstat(file_descriptor)
    finally:
        for file_descriptor in opened_descriptors:
            try:
                real_close(file_descriptor)
            except OSError:
                pass


def test_unlock_failure_still_closes_all_directory_descriptors(tmp_path, monkeypatch):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    descriptor_count_before = len(os.listdir("/proc/self/fd"))
    real_flock = fcntl.flock

    def fail_unlock(file_descriptor, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError("injected unlock failure")
        return real_flock(file_descriptor, operation)

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.fcntl.flock",
        fail_unlock,
    )

    recovered = service.get_settings()

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert len(os.listdir("/proc/self/fd")) == descriptor_count_before


def test_atomic_save_fsyncs_file_and_containing_directory(tmp_path, monkeypatch):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    fsync_modes = []
    real_fsync = os.fsync

    def record_fsync(file_descriptor):
        fsync_modes.append(stat.S_IFMT(os.fstat(file_descriptor).st_mode))
        real_fsync(file_descriptor)

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.fsync", record_fsync
    )

    service.set_api_key("aihubmix", "thumbnail-secret")

    assert stat.S_IFREG in fsync_modes
    assert stat.S_IFDIR in fsync_modes


@pytest.mark.parametrize(
    "settings_path",
    [
        Path("thumbnail_prompt/settings.toml"),
        Path("/settings.toml"),
        Path.cwd() / "thumbnail_prompt" / "settings.toml",
        Path("/tmp/thumbnail_prompt/settings.toml"),
        Path("/tmp/not-thumbnail-prompt/settings.toml"),
        Path("/tmp/thumbnail_prompt/not-settings.toml"),
    ],
)
def test_settings_path_must_target_an_absolute_dedicated_leaf(settings_path):
    with pytest.raises(ValueError, match="dedicated Thumbnail Prompt settings path"):
        ThumbnailPromptSettingsService(settings_path=settings_path)


def test_existing_parent_and_dedicated_leaf_permissions_are_unchanged(tmp_path):
    parent = tmp_path / "caller-owned"
    parent.mkdir(mode=0o751)
    settings_dir = parent / "thumbnail_prompt"
    settings_dir.mkdir(mode=0o755)
    parent.chmod(0o751)
    settings_dir.chmod(0o755)

    ThumbnailPromptSettingsService(
        settings_path=settings_dir / "settings.toml"
    ).get_settings()

    assert stat.S_IMODE(parent.stat().st_mode) == 0o751
    assert stat.S_IMODE(settings_dir.stat().st_mode) == 0o755
    assert stat.S_IMODE((settings_dir / ".settings.lock").stat().st_mode) == 0o600


def test_group_world_writable_dedicated_leaf_is_rejected_without_mutation(tmp_path):
    settings_dir = tmp_path / "thumbnail_prompt"
    settings_dir.mkdir(mode=0o700)
    settings_dir.chmod(0o777)
    settings_path = settings_dir / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    recovered = service.get_settings()
    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("aihubmix", "must-not-be-written")

    assert recovered.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert stat.S_IMODE(settings_dir.stat().st_mode) == 0o777
    assert list(settings_dir.iterdir()) == []


def test_leaf_rename_replacement_before_commit_aborts_without_detached_write(tmp_path):
    settings_dir = tmp_path / "thumbnail_prompt"
    settings_path = settings_dir / "settings.toml"
    detached_dir = tmp_path / "thumbnail_prompt-detached"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    real_verify_lock = service._verify_lock_identity_locked
    replaced = False

    def replace_leaf_after_lock_check(directory_fd, lock_fd):
        nonlocal replaced
        real_verify_lock(directory_fd, lock_fd)
        temporary_exists = any(
            name.startswith(".settings.toml.") and name.endswith(".tmp")
            for name in os.listdir(directory_fd)
        )
        if temporary_exists and not replaced:
            replaced = True
            settings_dir.rename(detached_dir)
            settings_dir.mkdir(mode=0o700)

    service._verify_lock_identity_locked = replace_leaf_after_lock_check

    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("aihubmix", "must-not-be-written")

    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert not settings_path.exists()
    assert not (detached_dir / "settings.toml").exists()
    assert list(settings_dir.iterdir()) == []
    assert list(detached_dir.glob(".settings.toml.*.tmp")) == []


def test_leaf_replacement_after_save_is_detected_before_returning_success(tmp_path):
    settings_dir = tmp_path / "thumbnail_prompt"
    settings_path = settings_dir / "settings.toml"
    detached_dir = tmp_path / "thumbnail_prompt-after-save"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    real_save = service._save_locked

    def save_then_replace_leaf(*args, **kwargs):
        real_save(*args, **kwargs)
        settings_dir.rename(detached_dir)
        settings_dir.mkdir(mode=0o700)

    service._save_locked = save_then_replace_leaf

    with pytest.raises(ThumbnailPromptError) as error:
        service.set_api_key("aihubmix", "saved-before-leaf-replacement")

    assert error.value.code == "THUMBNAIL_PROMPT_SETTINGS_UNSAFE"
    assert not settings_path.exists()
    assert (detached_dir / "settings.toml").exists()


def test_only_dedicated_leaf_may_be_created(tmp_path):
    settings_path = tmp_path / "missing-parent" / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    settings = service.get_settings()

    assert settings.configuration_error == _CORRUPT_SETTINGS_MESSAGE
    assert not (tmp_path / "missing-parent").exists()


def test_production_factory_does_not_invoke_creating_storage_helper(
    tmp_path, monkeypatch
):
    captured = {}

    class CapturingSettingsService:
        def __init__(self, *, settings_path):
            captured["settings_path"] = settings_path

    def fail_storage_helper():
        raise AssertionError("factory must not pre-create Thumbnail Prompt storage")

    monkeypatch.setattr(factory, "CloudJobStorage", fail_storage_helper)
    monkeypatch.setattr(
        factory,
        "utils",
        type(
            "Utils",
            (),
            {"storage_dir": staticmethod(lambda: str(tmp_path / "storage"))},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        factory, "ThumbnailPromptSettingsService", CapturingSettingsService
    )

    service = factory.build_thumbnail_prompt_settings_service()

    assert isinstance(service, CapturingSettingsService)
    assert captured["settings_path"] == (
        tmp_path / "storage" / "thumbnail_prompt" / "settings.toml"
    )


def test_main_config_defines_no_thumbnail_prompt_defaults():
    assert not hasattr(config, "THUMBNAIL_PROMPT_DEFAULTS")
    defaults = config._apply_cloud_agent_defaults({})
    assert not any(key.startswith("cloud_agent_thumbnail_prompt_") for key in defaults)


def test_saving_settings_and_api_key_never_mutates_main_config(tmp_path, monkeypatch):
    main_config = tmp_path / "config.toml"
    main_config.write_bytes(b'[app]\nexisting = "unchanged"\n')
    original_bytes = main_config.read_bytes()
    original_mtime_ns = main_config.stat().st_mtime_ns

    def fail_save_config():
        raise AssertionError("thumbnail settings must not call config.save_config")

    monkeypatch.setattr(config, "save_config", fail_save_config)
    service = ThumbnailPromptSettingsService(
        settings_path=tmp_path / "thumbnail_prompt" / "settings.toml"
    )

    service.update_settings(
        ThumbnailPromptSettingsPayload(
            master_prompt="Create a striking thumbnail.",
            default_provider="openrouter",
            aihubmix_model="gpt-5.6-sol",
            aihubmix_custom_model_id="",
            aihubmix_base_url="https://aihubmix.example/v1",
            openrouter_model="openai/gpt-5.6-sol",
            openrouter_custom_model_id="",
            openrouter_base_url="https://openrouter.example/api/v1",
        )
    )
    service.set_api_key("openrouter", "thumbnail-only-secret")

    assert main_config.read_bytes() == original_bytes
    assert main_config.stat().st_mtime_ns == original_mtime_ns


def test_settings_are_atomically_persisted_with_restrictive_permissions(
    tmp_path, monkeypatch
):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    service = ThumbnailPromptSettingsService(settings_path=settings_path)
    replace_calls = []
    real_replace = os.replace

    def record_replace(source, destination, **kwargs):
        replace_calls.append((source, destination, kwargs))
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.replace", record_replace
    )

    service.set_api_key("aihubmix", "thumbnail-secret")

    assert len(replace_calls) == 1
    assert replace_calls[0][1] == settings_path.name
    assert replace_calls[0][0] != settings_path.name
    assert replace_calls[0][2]["src_dir_fd"] == replace_calls[0][2]["dst_dir_fd"]
    assert toml.load(settings_path)["aihubmix_api_key"] == "thumbnail-secret"
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600
    assert list(settings_path.parent.glob(f".{settings_path.name}.*")) == []


def test_production_factory_uses_dedicated_storage_path(tmp_path, monkeypatch):
    captured = {}

    class CapturingSettingsService:
        def __init__(self, *, settings_path):
            captured["settings_path"] = settings_path

    monkeypatch.setattr(factory.utils, "storage_dir", lambda: str(tmp_path / "storage"))
    monkeypatch.setattr(
        factory, "ThumbnailPromptSettingsService", CapturingSettingsService
    )

    service = factory.build_thumbnail_prompt_settings_service()

    assert isinstance(service, CapturingSettingsService)
    assert captured["settings_path"] == (
        tmp_path / "storage" / "thumbnail_prompt" / "settings.toml"
    )


def test_generation_snapshot_is_resolved_validated_and_immutable(tmp_path):
    service = ThumbnailPromptSettingsService(
        settings_path=tmp_path / "thumbnail_prompt" / "settings.toml"
    )
    service.update_settings(
        ThumbnailPromptSettingsPayload(
            master_prompt="Create a striking thumbnail.",
            default_provider="openrouter",
            aihubmix_model="gpt-5.6-sol",
            aihubmix_custom_model_id="",
            aihubmix_base_url="https://aihubmix.example/v1",
            openrouter_model="custom",
            openrouter_custom_model_id="thumbnail/custom-model",
            openrouter_base_url="https://openrouter.example/api/v1",
        )
    )
    service.set_api_key("openrouter", "snapshot-secret")

    snapshot = service.get_generation_snapshot()

    assert snapshot.provider_id == "openrouter"
    assert snapshot.api_key.get_secret_value() == "snapshot-secret"
    assert snapshot.model_id == "thumbnail/custom-model"
    assert snapshot.base_url == "https://openrouter.example/api/v1"
    assert snapshot.master_prompt == "Create a striking thumbnail."
    assert "snapshot-secret" not in repr(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.model_id = "changed"


def test_generation_snapshot_holds_subsystem_lock_through_resolution(tmp_path):
    service = ThumbnailPromptSettingsService(
        settings_path=tmp_path / "thumbnail_prompt" / "settings.toml"
    )
    service.update_settings(_valid_payload(master_prompt="Thumbnail direction"))
    service.set_api_key("aihubmix", "snapshot-key")
    original_validate_model = service._validate_model
    writer_started = threading.Event()
    writer_finished = threading.Event()
    writer_threads = []
    writer_was_blocked = []

    def write_other_key():
        writer_started.set()
        service.set_api_key("openrouter", "concurrent-key")
        writer_finished.set()

    def validate_while_writer_waits(*args, **kwargs):
        writer = threading.Thread(target=write_other_key)
        writer_threads.append(writer)
        writer.start()
        assert writer_started.wait(timeout=1)
        writer_was_blocked.append(not writer_finished.wait(timeout=0.2))
        return original_validate_model(*args, **kwargs)

    service._validate_model = validate_while_writer_waits

    snapshot = service.get_generation_snapshot()
    for writer in writer_threads:
        writer.join(timeout=5)

    assert snapshot.model_id == "gpt-5.6-sol"
    assert writer_was_blocked == [True]
    assert writer_finished.is_set()


def test_defaults_are_dedicated_and_aihubmix_is_selected(tmp_path):
    service = ThumbnailPromptSettingsService(
        settings_path=tmp_path / "thumbnail_prompt" / "settings.toml"
    )

    assert service.get_settings().default_provider == "aihubmix"
    assert service.get_provider("aihubmix").default_model == "gpt-5.6-sol"
    assert service.get_provider("openrouter").default_model == "openai/gpt-5.6-sol"


def test_settings_hide_api_key_and_allow_custom_model(tmp_path):
    service = ThumbnailPromptSettingsService(
        settings_path=tmp_path / "thumbnail_prompt" / "settings.toml"
    )
    service.set_api_key("aihubmix", "secret-value")

    updated = service.update_settings(
        ThumbnailPromptSettingsPayload(
            master_prompt="Create a striking thumbnail.",
            default_provider="aihubmix",
            aihubmix_model="custom",
            aihubmix_custom_model_id="my-thumbnail-model",
            aihubmix_base_url="https://aihubmix.example/v1",
            openrouter_model="openai/gpt-5.6-sol",
            openrouter_custom_model_id="",
            openrouter_base_url="https://openrouter.example/api/v1",
        )
    )

    assert updated.aihubmix_model == "custom"
    assert updated.aihubmix_base_url == "https://aihubmix.example/v1"
    assert updated.openrouter_base_url == "https://openrouter.example/api/v1"
    assert "secret-value" not in updated.model_dump_json()


@pytest.mark.parametrize("configured", ["", "not-a-provider"])
def test_invalid_configured_default_provider_is_not_silently_replaced(
    tmp_path, configured
):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    settings_path.write_text(toml.dumps({"default_provider": configured}))
    settings_path.chmod(0o600)

    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptSettingsService(
            settings_path=settings_path
        ).get_configured_provider_id()

    assert error.value.code == "PROVIDER_UNSUPPORTED"


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "thumbnail-provider.invalid/v1",
        "ftp://thumbnail-provider.invalid/v1",
        "https://secret@thumbnail-provider.invalid/v1",
        "https://thumbnail-provider.invalid/v1?api_key=secret",
    ],
)
def test_invalid_provider_base_url_is_rejected_for_generation(tmp_path, base_url):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    settings_path.write_text(toml.dumps({"aihubmix_base_url": base_url}))
    settings_path.chmod(0o600)

    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptSettingsService(
            settings_path=settings_path
        ).get_base_url_for_generation("aihubmix")

    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
