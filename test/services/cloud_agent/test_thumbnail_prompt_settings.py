import pytest

from app.config import config
from app.config.config import THUMBNAIL_PROMPT_DEFAULTS
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)
from app.services.cloud_agent.thumbnail_prompt.models import (
    ThumbnailPromptSettingsPayload,
)


def test_defaults_are_dedicated_and_aihubmix_is_selected(monkeypatch):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))

    service = ThumbnailPromptSettingsService()

    assert service.get_settings().default_provider == "aihubmix"
    assert service.get_provider("aihubmix").default_model == "gpt-5.6-sol"
    assert service.get_provider("openrouter").default_model == "openai/gpt-5.6-sol"


def test_settings_hide_api_key_and_allow_custom_model(monkeypatch):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))
    service = ThumbnailPromptSettingsService()
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
    monkeypatch, configured
):
    app_config = dict(THUMBNAIL_PROMPT_DEFAULTS)
    app_config["cloud_agent_thumbnail_prompt_default_provider"] = configured
    monkeypatch.setattr(config, "app", app_config)

    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptSettingsService().get_configured_provider_id()

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
def test_invalid_provider_base_url_is_rejected_for_generation(monkeypatch, base_url):
    app_config = dict(THUMBNAIL_PROMPT_DEFAULTS)
    app_config["cloud_agent_thumbnail_prompt_aihubmix_base_url"] = base_url
    monkeypatch.setattr(config, "app", app_config)

    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptSettingsService().get_base_url_for_generation("aihubmix")

    assert error.value.code == "PROVIDER_BASE_URL_INVALID"
