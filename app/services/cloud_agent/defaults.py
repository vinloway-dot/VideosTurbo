"""Persisted operator defaults for the Cloud Agent WebUI."""

from app.config import config
from app.models.cloud_agent import CloudAgentDefaults, CloudAgentDefaultsPatch


_DEFAULT_KEYS = {
    "tts_provider": "cloud_agent_default_tts_provider",
    "voice_id": "cloud_agent_default_voice_id",
    "voice_speed": "cloud_agent_default_voice_speed",
    "custom_system_prompt": "cloud_agent_default_custom_system_prompt",
    "create_canva_captions": "cloud_agent_default_create_canva_captions",
}


class CloudAgentDefaultsError(ValueError):
    """Raised when a default refers to an unsupported TTS provider."""


class CloudAgentDefaultsService:
    def __init__(self, supported_providers: set[str]):
        self.supported_providers = supported_providers

    def get(self) -> CloudAgentDefaults:
        return CloudAgentDefaults(
            tts_provider=str(config.app.get(_DEFAULT_KEYS["tts_provider"], "azure-tts-v1")),
            voice_id=str(config.app.get(_DEFAULT_KEYS["voice_id"], "")),
            voice_speed=float(config.app.get(_DEFAULT_KEYS["voice_speed"], 1.0)),
            custom_system_prompt=str(config.app.get(_DEFAULT_KEYS["custom_system_prompt"], "")),
            create_canva_captions=bool(
                config.app.get(_DEFAULT_KEYS["create_canva_captions"], False)
            ),
        )

    def update(self, patch: CloudAgentDefaultsPatch) -> CloudAgentDefaults:
        if patch.tts_provider not in self.supported_providers:
            raise CloudAgentDefaultsError("unsupported TTS provider")
        with config.runtime_config_lock():
            for name, key in _DEFAULT_KEYS.items():
                config.app[key] = getattr(patch, name)
            config.save_config()
        return self.get()

    def reset(self) -> CloudAgentDefaults:
        with config.runtime_config_lock():
            for key in _DEFAULT_KEYS.values():
                config.app.pop(key, None)
            config.save_config()
        return self.get()
