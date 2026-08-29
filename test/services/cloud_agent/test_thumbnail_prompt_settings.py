from app.config import config
from app.config.config import THUMBNAIL_PROMPT_DEFAULTS
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)
from app.services.cloud_agent.thumbnail_prompt.models import ThumbnailPromptSettingsPayload


def test_defaults_are_dedicated_and_aihubmix_is_selected(monkeypatch):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))

    service = ThumbnailPromptSettingsService()

    assert service.get_settings().default_provider == "aihubmix"
    assert service.get_provider("aihubmix").default_model == "gpt-5.6-sol"
    assert (
        service.get_provider("openrouter").default_model == "openai/gpt-5.6-sol"
    )


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
            openrouter_model="openai/gpt-5.6-sol",
            openrouter_custom_model_id="",
        )
    )

    assert updated.aihubmix_model == "custom"
    assert "secret-value" not in updated.model_dump_json()
