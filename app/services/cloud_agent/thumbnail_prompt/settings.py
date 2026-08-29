"""Package-owned configuration and credential storage for Thumbnail Prompt."""

import os
from pathlib import Path
import tempfile
import threading
import unicodedata
from urllib.parse import urlsplit

from pydantic import SecretStr
import toml

from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.models import (
    ThumbnailPromptGenerationSettings,
    ThumbnailPromptProviderMetadata,
    ThumbnailPromptSettings,
    ThumbnailPromptSettingsPayload,
)


_PROVIDERS = {
    "aihubmix": {
        "label": "AIHubMix",
        "default_model": "gpt-5.6-sol",
        "api_key_name": "aihubmix_api_key",
        "base_url_name": "aihubmix_base_url",
        "model_name": "aihubmix_model",
        "custom_model_name": "aihubmix_custom_model",
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_model": "openai/gpt-5.6-sol",
        "api_key_name": "openrouter_api_key",
        "base_url_name": "openrouter_base_url",
        "model_name": "openrouter_model",
        "custom_model_name": "openrouter_custom_model",
    },
}
_DEFAULTS = {
    "master_prompt": "",
    "default_provider": "aihubmix",
    "aihubmix_model": "gpt-5.6-sol",
    "aihubmix_custom_model": "",
    "aihubmix_api_key": "",
    "aihubmix_base_url": "https://aihubmix.com/v1",
    "openrouter_model": "openai/gpt-5.6-sol",
    "openrouter_custom_model": "",
    "openrouter_api_key": "",
    "openrouter_base_url": "https://openrouter.ai/api/v1",
}
_SETTINGS_LOCK = threading.RLock()
_INVALID_DEFAULT_PROVIDER_MESSAGE = (
    "Saved default thumbnail provider is invalid. "
    "Select AIHubMix or OpenRouter and save Thumbnail Prompt Settings."
)
_INVALID_BASE_URL_MESSAGE = (
    "Saved thumbnail provider base URL is invalid. "
    "Enter valid HTTP(S) Base URLs and save Thumbnail Prompt Settings."
)


class ThumbnailPromptSettingsService:
    """Own Thumbnail Prompt settings without sharing application configuration."""

    DEFAULT_PROVIDER_ID = "aihubmix"
    KEY_NAMES = {
        provider_id: str(metadata["api_key_name"])
        for provider_id, metadata in _PROVIDERS.items()
    }

    def __init__(self, *, settings_path: Path) -> None:
        self._settings_path = Path(settings_path)

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def list_providers(self) -> list[ThumbnailPromptProviderMetadata]:
        with _SETTINGS_LOCK:
            configured = self._load_locked()
            return [
                self._provider_from_config(provider_id, configured)
                for provider_id in _PROVIDERS
            ]

    def get_provider(self, provider_id: str) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        with _SETTINGS_LOCK:
            return self._provider_from_config(normalized, self._load_locked())

    def get_settings(self) -> ThumbnailPromptSettings:
        with _SETTINGS_LOCK:
            return self._settings_from_config(self._load_locked())

    def get_configured_provider_id(self) -> str:
        with _SETTINGS_LOCK:
            configured = self._configured_text(
                self._load_locked(), "default_provider"
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

        with _SETTINGS_LOCK:
            configured = self._load_locked()
            configured.update(
                {
                    "master_prompt": payload.master_prompt,
                    "default_provider": payload.default_provider,
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
            self._save_locked(configured)
            return self._settings_from_config(configured)

    def set_api_key(
        self, provider_id: str, value: str
    ) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        cleaned_value = str(value or "").strip()
        if not cleaned_value:
            return self.get_provider(normalized)

        with _SETTINGS_LOCK:
            configured = self._load_locked()
            configured[self.KEY_NAMES[normalized]] = cleaned_value
            self._save_locked(configured)
            return self._provider_from_config(normalized, configured)

    def remove_api_key(
        self, provider_id: str, confirmed: bool
    ) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        if confirmed is not True:
            raise ThumbnailPromptError(
                "THUMBNAIL_PROMPT_REQUEST_INVALID", "key removal not confirmed"
            )
        with _SETTINGS_LOCK:
            configured = self._load_locked()
            configured[self.KEY_NAMES[normalized]] = ""
            self._save_locked(configured)
            return self._provider_from_config(normalized, configured)

    def get_api_key_for_generation(self, provider_id: str) -> SecretStr:
        normalized = self._require_provider(provider_id)
        with _SETTINGS_LOCK:
            value = self._configured_text(
                self._load_locked(), self.KEY_NAMES[normalized]
            )
        if not value:
            raise ThumbnailPromptError(
                "PROVIDER_API_KEY_MISSING",
                f"{normalized} provider key is not configured",
            )
        return SecretStr(value)

    def resolve_model(self, provider_id: str) -> str:
        normalized = self._require_provider(provider_id)
        metadata = _PROVIDERS[normalized]
        with _SETTINGS_LOCK:
            configured = self._load_locked()
            choice = self._configured_text(configured, metadata["model_name"])
            custom_model = self._configured_text(
                configured, metadata["custom_model_name"]
            )
        self._validate_model(normalized, choice, custom_model)
        return custom_model if choice == "custom" else choice

    def get_base_url_for_generation(self, provider_id: str) -> str:
        normalized = self._require_provider(provider_id)
        with _SETTINGS_LOCK:
            value = self._configured_text(
                self._load_locked(), _PROVIDERS[normalized]["base_url_name"]
            )
        return self._validate_base_url(normalized, value)

    def get_generation_snapshot(self) -> ThumbnailPromptGenerationSettings:
        with _SETTINGS_LOCK:
            configured = self._load_locked()
            provider_id = self._require_provider(
                self._configured_text(configured, "default_provider")
            )
            metadata = _PROVIDERS[provider_id]
            api_key_value = self._configured_text(
                configured, metadata["api_key_name"]
            )
            if not api_key_value:
                raise ThumbnailPromptError(
                    "PROVIDER_API_KEY_MISSING",
                    f"{provider_id} provider key is not configured",
                )
            model_choice = self._configured_text(configured, metadata["model_name"])
            custom_model = self._configured_text(
                configured, metadata["custom_model_name"]
            )
            self._validate_model(provider_id, model_choice, custom_model)
            model_id = custom_model if model_choice == "custom" else model_choice
            base_url = self._validate_base_url(
                provider_id,
                self._configured_text(configured, metadata["base_url_name"]),
            )
            master_prompt = self._configured_text(configured, "master_prompt")
            if not master_prompt:
                raise ThumbnailPromptError(
                    "THUMBNAIL_MASTER_PROMPT_MISSING",
                    "ยังไม่ได้ตั้งค่า Thumbnail Master Prompt",
                )
            return ThumbnailPromptGenerationSettings(
                provider_id=provider_id,
                api_key=SecretStr(api_key_value),
                model_id=model_id,
                base_url=base_url,
                master_prompt=master_prompt,
            )

    def _provider_from_config(
        self, provider_id: str, configured: dict[str, object]
    ) -> ThumbnailPromptProviderMetadata:
        metadata = _PROVIDERS[provider_id]
        readable_base_url, _ = self._readable_base_url(provider_id, configured)
        return ThumbnailPromptProviderMetadata(
            id=provider_id,
            label=str(metadata["label"]),
            models=[str(metadata["default_model"]), "custom"],
            default_model=str(metadata["default_model"]),
            custom_model_id=self._configured_text(
                configured, metadata["custom_model_name"]
            ),
            base_url=readable_base_url,
            api_key_configured=bool(
                self._configured_text(configured, metadata["api_key_name"])
            ),
        )

    def _settings_from_config(
        self, configured: dict[str, object]
    ) -> ThumbnailPromptSettings:
        configured_provider = self._configured_text(configured, "default_provider")
        readable_provider = (
            configured_provider if configured_provider in _PROVIDERS else None
        )
        aihubmix_base_url, aihubmix_base_url_valid = self._readable_base_url(
            "aihubmix", configured
        )
        openrouter_base_url, openrouter_base_url_valid = self._readable_base_url(
            "openrouter", configured
        )
        configuration_errors = []
        if not readable_provider:
            configuration_errors.append(_INVALID_DEFAULT_PROVIDER_MESSAGE)
        if not (aihubmix_base_url_valid and openrouter_base_url_valid):
            configuration_errors.append(_INVALID_BASE_URL_MESSAGE)
        return ThumbnailPromptSettings(
            master_prompt=self._configured_text(configured, "master_prompt"),
            default_provider=readable_provider,
            configuration_error=" ".join(configuration_errors) or None,
            aihubmix_model=self._configured_text(
                configured, _PROVIDERS["aihubmix"]["model_name"]
            ),
            aihubmix_custom_model_id=self._configured_text(
                configured, _PROVIDERS["aihubmix"]["custom_model_name"]
            ),
            aihubmix_base_url=aihubmix_base_url,
            openrouter_model=self._configured_text(
                configured, _PROVIDERS["openrouter"]["model_name"]
            ),
            openrouter_custom_model_id=self._configured_text(
                configured, _PROVIDERS["openrouter"]["custom_model_name"]
            ),
            openrouter_base_url=openrouter_base_url,
        )

    def _validate_model(
        self, provider_id: str, model_choice: str, custom_model_id: str
    ) -> None:
        normalized = self._require_provider(provider_id)
        choice = str(model_choice or "").strip()
        allowed_models = [str(_PROVIDERS[normalized]["default_model"]), "custom"]
        if choice not in allowed_models:
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
            or any(
                character == "\\" or unicodedata.category(character) == "Cc"
                for character in normalized
            )
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

    def _readable_base_url(
        self, provider_id: str, configured: dict[str, object]
    ) -> tuple[str, bool]:
        metadata = _PROVIDERS[provider_id]
        value = self._configured_text(configured, metadata["base_url_name"])
        try:
            return self._validate_base_url(provider_id, value), True
        except ThumbnailPromptError:
            return "", False

    @staticmethod
    def _configured_text(configured: dict[str, object], key: object) -> str:
        return str(configured.get(str(key), "") or "").strip()

    def _load_locked(self) -> dict[str, object]:
        configured: dict[str, object] = dict(_DEFAULTS)
        try:
            persisted = toml.load(self._settings_path)
        except (OSError, TypeError, toml.TomlDecodeError):
            return configured
        if isinstance(persisted, dict):
            configured.update(
                {key: persisted[key] for key in _DEFAULTS if key in persisted}
            )
        return configured

    def _save_locked(self, configured: dict[str, object]) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._settings_path.name}.",
            suffix=".tmp",
            dir=self._settings_path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
                temporary_file.write(toml.dumps(configured))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self._settings_path)
            os.chmod(self._settings_path, 0o600)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
