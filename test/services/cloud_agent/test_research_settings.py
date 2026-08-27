import pytest

from app.config import config
from app.services.cloud_agent import factory
from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.settings import ResearchSettingsService


def test_settings_readback_redacts_key_and_blank_save_retains_it(monkeypatch):
    saved = []
    monkeypatch.setitem(config.app, "cloud_agent_research_openrouter_api_key", "")
    monkeypatch.setattr(config, "save_config", lambda: saved.append(True))
    service = factory.build_research_settings_service()

    service.set_api_key("openrouter", "  secret-value  ")
    service.set_api_key("openrouter", "   ")
    metadata = service.get_provider("openrouter")

    assert isinstance(service, ResearchSettingsService)
    assert metadata.api_key_configured is True
    assert "secret-value" not in metadata.model_dump_json()
    assert (
        service.get_api_key_for_generation("openrouter").get_secret_value()
        == "secret-value"
    )
    assert saved == [True]


def test_key_removal_requires_confirmation_and_then_clears_configured_state(
    monkeypatch,
):
    saved = []
    monkeypatch.setitem(config.app, "cloud_agent_research_aihubmix_api_key", "")
    monkeypatch.setattr(config, "save_config", lambda: saved.append(True))
    service = factory.build_research_settings_service()

    service.set_api_key("aihubmix", "secret-value")
    with pytest.raises(ResearchError) as excinfo:
        service.remove_api_key("aihubmix", confirmed=False)

    assert excinfo.value.code == "RESEARCH_RESPONSE_INVALID"
    removed = service.remove_api_key("aihubmix", confirmed=True)

    assert removed.api_key_configured is False
    assert saved == [True, True]


def test_generation_key_lookup_raises_typed_error_when_missing(monkeypatch):
    monkeypatch.setitem(config.app, "cloud_agent_research_openrouter_api_key", "")
    service = factory.build_research_settings_service()

    with pytest.raises(ResearchError) as excinfo:
        service.get_api_key_for_generation("openrouter")

    assert excinfo.value.code == "PROVIDER_API_KEY_MISSING"


def test_provider_catalog_exposes_model_default_and_custom_handoff(monkeypatch):
    monkeypatch.setitem(
        config.app,
        "cloud_agent_research_openrouter_custom_model",
        "custom/openrouter-model",
    )
    metadata = ResearchSettingsService().get_provider("openrouter")

    assert metadata.models == ["openai/gpt-5.6-sol-pro", "custom"]
    assert metadata.default_model == "openai/gpt-5.6-sol-pro"
    assert metadata.custom_model_id == "custom/openrouter-model"


def test_invalid_configured_default_provider_normalizes_to_openrouter(monkeypatch):
    saved = []
    monkeypatch.setitem(config.app, "cloud_agent_research_default_provider", "unsupported")
    monkeypatch.setattr(config, "save_config", lambda: saved.append(True))
    service = ResearchSettingsService()

    assert service.get_configured_provider_id() == "openrouter"
    assert config.app["cloud_agent_research_default_provider"] == "openrouter"
    assert saved == [True]


def test_valid_configured_default_provider_is_preserved_without_resave(monkeypatch):
    saved = []
    monkeypatch.setitem(config.app, "cloud_agent_research_default_provider", "aihubmix")
    monkeypatch.setattr(config, "save_config", lambda: saved.append(True))
    service = ResearchSettingsService()

    assert service.get_configured_provider_id() == "aihubmix"
    assert config.app["cloud_agent_research_default_provider"] == "aihubmix"
    assert saved == []
