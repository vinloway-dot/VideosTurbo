import os
import stat
from dataclasses import FrozenInstanceError
import multiprocessing
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
):
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
    settings_path.parent.mkdir()
    settings_path.write_bytes(persisted)
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
    settings_path.parent.mkdir()
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
    settings_path.parent.mkdir()
    corrupt_bytes = b'[broken = "corrupt-secret-marker"'
    settings_path.write_bytes(corrupt_bytes)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    updated = service.update_settings(_valid_payload())

    assert updated.configuration_error is None
    assert toml.load(settings_path)["master_prompt"] == "Create a striking thumbnail."
    preserved = list(settings_path.parent.glob(".corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt_bytes


def test_successful_api_key_repair_preserves_corrupt_file(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir()
    corrupt_bytes = b"\xffcorrupt-key-file"
    settings_path.write_bytes(corrupt_bytes)
    service = ThumbnailPromptSettingsService(settings_path=settings_path)

    provider = service.set_api_key("openrouter", "repaired-key")

    assert provider.api_key_configured is True
    assert toml.load(settings_path)["openrouter_api_key"] == "repaired-key"
    preserved = list(settings_path.parent.glob(".corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt_bytes


def test_failed_validation_does_not_overwrite_or_preserve_corrupt_file(tmp_path):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir()
    corrupt_bytes = b"[invalid"
    settings_path.write_bytes(corrupt_bytes)
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

    first.start()
    assert first_started.wait(timeout=5)
    assert first_save_entered.wait(timeout=5)
    second.start()
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
    settings_dir.mkdir()
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
    settings_dir.mkdir()
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
    service = ThumbnailPromptSettingsService(settings_path=tmp_path / "settings.toml")
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
    service = ThumbnailPromptSettingsService(settings_path=tmp_path / "settings.toml")
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
    service = ThumbnailPromptSettingsService(settings_path=tmp_path / "settings.toml")

    assert service.get_settings().default_provider == "aihubmix"
    assert service.get_provider("aihubmix").default_model == "gpt-5.6-sol"
    assert service.get_provider("openrouter").default_model == "openai/gpt-5.6-sol"


def test_settings_hide_api_key_and_allow_custom_model(tmp_path):
    service = ThumbnailPromptSettingsService(settings_path=tmp_path / "settings.toml")
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
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(toml.dumps({"default_provider": configured}))

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
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(toml.dumps({"aihubmix_base_url": base_url}))

    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptSettingsService(
            settings_path=settings_path
        ).get_base_url_for_generation("aihubmix")

    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
