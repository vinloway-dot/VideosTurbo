import os
import stat
from dataclasses import FrozenInstanceError

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

    def record_replace(source, destination):
        replace_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(
        "app.services.cloud_agent.thumbnail_prompt.settings.os.replace", record_replace
    )

    service.set_api_key("aihubmix", "thumbnail-secret")

    assert len(replace_calls) == 1
    assert replace_calls[0][1] == settings_path
    assert replace_calls[0][0] != settings_path
    assert toml.load(settings_path)["aihubmix_api_key"] == "thumbnail-secret"
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600
    assert list(settings_path.parent.glob(f".{settings_path.name}.*")) == []


def test_production_factory_uses_dedicated_storage_path(tmp_path, monkeypatch):
    captured = {}

    class CapturingSettingsService:
        def __init__(self, *, settings_path):
            captured["settings_path"] = settings_path

    monkeypatch.setattr(
        factory,
        "CloudJobStorage",
        lambda: type("Storage", (), {"root": tmp_path / "storage" / "jobs"})(),
    )
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
