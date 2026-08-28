from pydantic import BaseModel, SecretStr

from app.config import config
from app.services.cloud_agent.research.errors import ResearchError


_PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter",
        "api_key_name": "cloud_agent_research_openrouter_api_key",
        "models": ["openai/gpt-5.6-sol-pro", "custom"],
        "default_model": "openai/gpt-5.6-sol-pro",
        "custom_model_name": "cloud_agent_research_openrouter_custom_model",
    },
    "aihubmix": {
        "label": "AIHubMix",
        "api_key_name": "cloud_agent_research_aihubmix_api_key",
        "models": ["gpt-5.6-sol", "custom"],
        "default_model": "gpt-5.6-sol",
        "custom_model_name": "cloud_agent_research_aihubmix_custom_model",
    },
}


class ResearchProviderMetadata(BaseModel):
    id: str
    label: str
    models: list[str]
    default_model: str
    custom_model_id: str
    api_key_configured: bool


class ResearchSettingsService:
    DEFAULT_PROVIDER_ID = "aihubmix"
    KEY_NAMES = {
        provider_id: str(metadata["api_key_name"])
        for provider_id, metadata in _PROVIDERS.items()
    }

    def list_providers(self) -> list[ResearchProviderMetadata]:
        return [self.get_provider(provider_id) for provider_id in _PROVIDERS]

    def get_configured_provider_id(self) -> str:
        key_name = "cloud_agent_research_default_provider"
        with config.runtime_config_lock():
            configured = str(config.app.get(key_name, "") or "").strip()
            if configured in _PROVIDERS:
                return configured

            fallback = self.DEFAULT_PROVIDER_ID
            if configured == fallback:
                return fallback
            config.app[key_name] = fallback
            config.save_config()
            return fallback

    def get_provider(self, provider_id: str) -> ResearchProviderMetadata:
        normalized = self._require_provider(provider_id)
        metadata = _PROVIDERS[normalized]
        key_name = str(metadata["api_key_name"])
        return ResearchProviderMetadata(
            id=normalized,
            label=str(metadata["label"]),
            models=list(metadata["models"]),
            default_model=str(metadata["default_model"]),
            custom_model_id=str(
                config.app.get(str(metadata["custom_model_name"]), "") or ""
            ).strip(),
            api_key_configured=bool(str(config.app.get(key_name, "") or "").strip()),
        )

    def validate_model_choice(self, provider_id: str, model_choice: str) -> str:
        metadata = self.get_provider(provider_id)
        normalized = str(model_choice or "").strip()
        if normalized not in metadata.models:
            raise ResearchError(
                "PROVIDER_MODEL_UNSUPPORTED",
                f"unsupported catalog model choice for {metadata.id}",
            )
        return normalized

    def set_api_key(self, provider_id: str, value: str) -> ResearchProviderMetadata:
        normalized = self._require_provider(provider_id)
        key_name = self.KEY_NAMES[normalized]
        cleaned_value = str(value or "").strip()
        if not cleaned_value:
            return self.get_provider(normalized)

        with config.runtime_config_lock():
            config.app[key_name] = cleaned_value
            config.save_config()
        return self.get_provider(normalized)

    def remove_api_key(
        self, provider_id: str, confirmed: bool
    ) -> ResearchProviderMetadata:
        normalized = self._require_provider(provider_id)
        key_name = self.KEY_NAMES[normalized]
        if confirmed is not True:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "key removal not confirmed")

        with config.runtime_config_lock():
            config.app.pop(key_name, None)
            config.save_config()
        return self.get_provider(normalized)

    def get_api_key_for_generation(self, provider_id: str) -> SecretStr:
        normalized = self._require_provider(provider_id)
        key_name = self.KEY_NAMES[normalized]
        value = str(config.app.get(key_name, "") or "").strip()
        if not value:
            raise ResearchError(
                "PROVIDER_API_KEY_MISSING", f"{normalized} provider key is not configured"
            )
        return SecretStr(value)

    def _require_provider(self, provider_id: str) -> str:
        normalized = str(provider_id or "").strip()
        if normalized not in _PROVIDERS:
            raise ResearchError(
                "RESEARCH_RESPONSE_INVALID",
                f"unsupported research provider: {normalized or '<blank>'}",
            )
        return normalized
