from pydantic import BaseModel, SecretStr

from app.config import config
from app.services.cloud_agent.research.errors import ResearchError


_PROVIDERS = {
    "openrouter": ("OpenRouter", "cloud_agent_research_openrouter_api_key"),
    "aihubmix": ("AIHubMix", "cloud_agent_research_aihubmix_api_key"),
}


class ResearchProviderMetadata(BaseModel):
    id: str
    label: str
    api_key_configured: bool


class ResearchSettingsService:
    KEY_NAMES = {provider_id: key for provider_id, (_label, key) in _PROVIDERS.items()}

    def list_providers(self) -> list[ResearchProviderMetadata]:
        return [self.get_provider(provider_id) for provider_id in _PROVIDERS]

    def get_provider(self, provider_id: str) -> ResearchProviderMetadata:
        normalized = self._require_provider(provider_id)
        label, key_name = _PROVIDERS[normalized]
        return ResearchProviderMetadata(
            id=normalized,
            label=label,
            api_key_configured=bool(str(config.app.get(key_name, "") or "").strip()),
        )

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
