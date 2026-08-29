"""Configuration and credential ownership for Thumbnail Prompt."""

from pydantic import SecretStr

from app.config import config
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.models import (
    ThumbnailPromptProviderMetadata,
    ThumbnailPromptSettings,
    ThumbnailPromptSettingsPayload,
)


_PROVIDERS = {
    "aihubmix": {
        "label": "AIHubMix",
        "default_model": "gpt-5.6-sol",
        "api_key_name": "cloud_agent_thumbnail_prompt_aihubmix_api_key",
        "base_url_name": "cloud_agent_thumbnail_prompt_aihubmix_base_url",
        "model_name": "cloud_agent_thumbnail_prompt_aihubmix_model",
        "custom_model_name": "cloud_agent_thumbnail_prompt_aihubmix_custom_model",
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_model": "openai/gpt-5.6-sol",
        "api_key_name": "cloud_agent_thumbnail_prompt_openrouter_api_key",
        "base_url_name": "cloud_agent_thumbnail_prompt_openrouter_base_url",
        "model_name": "cloud_agent_thumbnail_prompt_openrouter_model",
        "custom_model_name": "cloud_agent_thumbnail_prompt_openrouter_custom_model",
    },
}


class ThumbnailPromptSettingsService:
    """Stores Thumbnail Prompt settings without sharing LLM credentials."""

    DEFAULT_PROVIDER_ID = "aihubmix"
    KEY_NAMES = {
        provider_id: str(metadata["api_key_name"])
        for provider_id, metadata in _PROVIDERS.items()
    }

    def list_providers(self) -> list[ThumbnailPromptProviderMetadata]:
        return [self.get_provider(provider_id) for provider_id in _PROVIDERS]

    def get_provider(self, provider_id: str) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        metadata = _PROVIDERS[normalized]
        return ThumbnailPromptProviderMetadata(
            id=normalized,
            label=str(metadata["label"]),
            models=[str(metadata["default_model"]), "custom"],
            default_model=str(metadata["default_model"]),
            custom_model_id=self._configured_text(metadata["custom_model_name"]),
            base_url=self._configured_text(metadata["base_url_name"]),
            api_key_configured=bool(self._configured_text(metadata["api_key_name"])),
        )

    def get_settings(self) -> ThumbnailPromptSettings:
        return ThumbnailPromptSettings(
            master_prompt=self._configured_text("cloud_agent_thumbnail_prompt_master_prompt"),
            default_provider=self.get_configured_provider_id(),
            aihubmix_model=self._configured_text(_PROVIDERS["aihubmix"]["model_name"]),
            aihubmix_custom_model_id=self._configured_text(
                _PROVIDERS["aihubmix"]["custom_model_name"]
            ),
            openrouter_model=self._configured_text(
                _PROVIDERS["openrouter"]["model_name"]
            ),
            openrouter_custom_model_id=self._configured_text(
                _PROVIDERS["openrouter"]["custom_model_name"]
            ),
        )

    def get_configured_provider_id(self) -> str:
        configured = self._configured_text("cloud_agent_thumbnail_prompt_default_provider")
        if configured in _PROVIDERS:
            return configured
        return self.DEFAULT_PROVIDER_ID

    def update_settings(
        self, payload: ThumbnailPromptSettingsPayload
    ) -> ThumbnailPromptSettings:
        self._require_provider(payload.default_provider)
        self._validate_model("aihubmix", payload.aihubmix_model, payload.aihubmix_custom_model_id)
        self._validate_model("openrouter", payload.openrouter_model, payload.openrouter_custom_model_id)

        with config.runtime_config_lock():
            config.app.update(
                {
                    "cloud_agent_thumbnail_prompt_master_prompt": payload.master_prompt,
                    "cloud_agent_thumbnail_prompt_default_provider": payload.default_provider,
                    _PROVIDERS["aihubmix"]["model_name"]: payload.aihubmix_model,
                    _PROVIDERS["aihubmix"]["custom_model_name"]: payload.aihubmix_custom_model_id,
                    _PROVIDERS["openrouter"]["model_name"]: payload.openrouter_model,
                    _PROVIDERS["openrouter"]["custom_model_name"]: payload.openrouter_custom_model_id,
                }
            )
            config.save_config()
        return self.get_settings()

    def set_api_key(
        self, provider_id: str, value: str
    ) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        cleaned_value = str(value or "").strip()
        if not cleaned_value:
            return self.get_provider(normalized)

        with config.runtime_config_lock():
            config.app[self.KEY_NAMES[normalized]] = cleaned_value
            config.save_config()
        return self.get_provider(normalized)

    def remove_api_key(
        self, provider_id: str, confirmed: bool
    ) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        if confirmed is not True:
            raise ThumbnailPromptError(
                "THUMBNAIL_PROMPT_REQUEST_INVALID", "key removal not confirmed"
            )
        with config.runtime_config_lock():
            config.app.pop(self.KEY_NAMES[normalized], None)
            config.save_config()
        return self.get_provider(normalized)

    def get_api_key_for_generation(self, provider_id: str) -> SecretStr:
        normalized = self._require_provider(provider_id)
        value = self._configured_text(self.KEY_NAMES[normalized])
        if not value:
            raise ThumbnailPromptError(
                "PROVIDER_API_KEY_MISSING",
                f"{normalized} provider key is not configured",
            )
        return SecretStr(value)

    def resolve_model(self, provider_id: str) -> str:
        normalized = self._require_provider(provider_id)
        metadata = _PROVIDERS[normalized]
        choice = self._configured_text(metadata["model_name"])
        custom_model = self._configured_text(metadata["custom_model_name"])
        self._validate_model(normalized, choice, custom_model)
        return custom_model if choice == "custom" else choice

    def _validate_model(
        self, provider_id: str, model_choice: str, custom_model_id: str
    ) -> None:
        normalized = self._require_provider(provider_id)
        choice = str(model_choice or "").strip()
        if choice not in self.get_provider(normalized).models:
            raise ThumbnailPromptError(
                "PROVIDER_MODEL_UNSUPPORTED",
                f"unsupported catalog model choice for {normalized}",
            )
        if choice == "custom" and not str(custom_model_id or "").strip():
            raise ThumbnailPromptError(
                "PROVIDER_CUSTOM_MODEL_REQUIRED",
                f"custom model id is required for {normalized}",
            )

    def _require_provider(self, provider_id: str) -> str:
        normalized = str(provider_id or "").strip()
        if normalized not in _PROVIDERS:
            raise ThumbnailPromptError(
                "PROVIDER_UNSUPPORTED",
                f"unsupported thumbnail prompt provider: {normalized or '<blank>'}",
            )
        return normalized

    @staticmethod
    def _configured_text(key: str) -> str:
        return str(config.app.get(key, "") or "").strip()
