"""Configuration and credential ownership for Thumbnail Prompt."""

from urllib.parse import urlsplit

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
_INVALID_DEFAULT_PROVIDER_MESSAGE = (
    "Saved default thumbnail provider is invalid. "
    "Select AIHubMix or OpenRouter and save Thumbnail Prompt Settings."
)
_INVALID_BASE_URL_MESSAGE = (
    "Saved thumbnail provider base URL is invalid. "
    "Enter valid HTTP(S) Base URLs and save Thumbnail Prompt Settings."
)


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
        readable_base_url, _ = self._readable_base_url(normalized)
        return ThumbnailPromptProviderMetadata(
            id=normalized,
            label=str(metadata["label"]),
            models=[str(metadata["default_model"]), "custom"],
            default_model=str(metadata["default_model"]),
            custom_model_id=self._configured_text(metadata["custom_model_name"]),
            base_url=readable_base_url,
            api_key_configured=bool(self._configured_text(metadata["api_key_name"])),
        )

    def get_settings(self) -> ThumbnailPromptSettings:
        configured_provider = self._configured_text(
            "cloud_agent_thumbnail_prompt_default_provider"
        )
        readable_provider = (
            configured_provider if configured_provider in _PROVIDERS else None
        )
        aihubmix_base_url, aihubmix_base_url_valid = self._readable_base_url("aihubmix")
        openrouter_base_url, openrouter_base_url_valid = self._readable_base_url(
            "openrouter"
        )
        configuration_errors = []
        if not readable_provider:
            configuration_errors.append(_INVALID_DEFAULT_PROVIDER_MESSAGE)
        if not (aihubmix_base_url_valid and openrouter_base_url_valid):
            configuration_errors.append(_INVALID_BASE_URL_MESSAGE)
        return ThumbnailPromptSettings(
            master_prompt=self._configured_text(
                "cloud_agent_thumbnail_prompt_master_prompt"
            ),
            default_provider=readable_provider,
            configuration_error=" ".join(configuration_errors) or None,
            aihubmix_model=self._configured_text(_PROVIDERS["aihubmix"]["model_name"]),
            aihubmix_custom_model_id=self._configured_text(
                _PROVIDERS["aihubmix"]["custom_model_name"]
            ),
            aihubmix_base_url=aihubmix_base_url,
            openrouter_model=self._configured_text(
                _PROVIDERS["openrouter"]["model_name"]
            ),
            openrouter_custom_model_id=self._configured_text(
                _PROVIDERS["openrouter"]["custom_model_name"]
            ),
            openrouter_base_url=openrouter_base_url,
        )

    def get_configured_provider_id(self) -> str:
        configured = self._configured_text(
            "cloud_agent_thumbnail_prompt_default_provider"
        )
        return self._require_provider(configured)

    def update_settings(
        self, payload: ThumbnailPromptSettingsPayload
    ) -> ThumbnailPromptSettings:
        self._require_provider(payload.default_provider)
        self._validate_model(
            "aihubmix", payload.aihubmix_model, payload.aihubmix_custom_model_id
        )
        self._validate_model(
            "openrouter", payload.openrouter_model, payload.openrouter_custom_model_id
        )
        aihubmix_base_url = self._validate_base_url(
            "aihubmix", payload.aihubmix_base_url
        )
        openrouter_base_url = self._validate_base_url(
            "openrouter", payload.openrouter_base_url
        )

        with config.runtime_config_lock():
            config.app.update(
                {
                    "cloud_agent_thumbnail_prompt_master_prompt": payload.master_prompt,
                    "cloud_agent_thumbnail_prompt_default_provider": payload.default_provider,
                    _PROVIDERS["aihubmix"]["model_name"]: payload.aihubmix_model,
                    _PROVIDERS["aihubmix"][
                        "custom_model_name"
                    ]: payload.aihubmix_custom_model_id,
                    _PROVIDERS["aihubmix"]["base_url_name"]: aihubmix_base_url,
                    _PROVIDERS["openrouter"]["model_name"]: payload.openrouter_model,
                    _PROVIDERS["openrouter"][
                        "custom_model_name"
                    ]: payload.openrouter_custom_model_id,
                    _PROVIDERS["openrouter"]["base_url_name"]: openrouter_base_url,
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

    def get_base_url_for_generation(self, provider_id: str) -> str:
        normalized = self._require_provider(provider_id)
        return self._validate_base_url(
            normalized,
            self._configured_text(_PROVIDERS[normalized]["base_url_name"]),
        )

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
    def _validate_base_url(provider_id: str, value: str) -> str:
        normalized = str(value or "").strip()
        try:
            parsed = urlsplit(normalized)
            hostname = parsed.hostname
            parsed.port
        except ValueError as exc:
            raise ThumbnailPromptError(
                "PROVIDER_BASE_URL_INVALID",
                f"invalid base URL for {provider_id}",
            ) from exc
        if (
            not normalized
            or len(normalized) > 2048
            or any(character.isspace() for character in normalized)
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            raise ThumbnailPromptError(
                "PROVIDER_BASE_URL_INVALID",
                f"invalid base URL for {provider_id}",
            )
        return normalized.rstrip("/")

    def _readable_base_url(self, provider_id: str) -> tuple[str, bool]:
        metadata = _PROVIDERS[provider_id]
        configured = self._configured_text(metadata["base_url_name"])
        try:
            return self._validate_base_url(provider_id, configured), True
        except ThumbnailPromptError:
            return "", False

    @staticmethod
    def _configured_text(key: str) -> str:
        return str(config.app.get(key, "") or "").strip()
