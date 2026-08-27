"""Safe Cloud Agent metadata for the repository's existing TTS providers."""

from app.config import config
from app.models.cloud_agent import TTSProviderMetadata, TTSSettingField, TTSVoiceOption
from app.services import voice


_PROVIDERS = (
    ("azure-tts-v1", "Azure TTS V1"),
    ("azure-tts-v2", "Azure TTS V2"),
    ("siliconflow", "SiliconFlow TTS"),
    ("gemini-tts", "Google Gemini TTS"),
    ("mimo-tts", "Xiaomi MiMo TTS"),
    ("minimax-tts", "MiniMax TTS"),
    ("elevenlabs", "ElevenLabs TTS"),
    ("chatterbox", "Chatterbox TTS"),
)


class CloudTTSSettingsError(ValueError):
    """Raised when a Cloud Agent TTS provider identifier is invalid."""


def _voice_options(values: list[str]) -> list[TTSVoiceOption]:
    return [TTSVoiceOption(id=value, label=value) for value in values]


def _password_field(name: str, label: str, configured: bool) -> TTSSettingField:
    return TTSSettingField(
        name=name,
        label=label,
        kind="password",
        value=None,
        configured=configured,
    )


class CloudTTSSettingsService:
    """Read safe provider metadata without exposing configuration credentials."""

    def list_providers(self) -> list[TTSProviderMetadata]:
        return [self.get_provider(provider_id) for provider_id, _label in _PROVIDERS]

    def get_provider(self, provider_id: str) -> TTSProviderMetadata:
        providers = dict(_PROVIDERS)
        if provider_id not in providers:
            raise CloudTTSSettingsError("unsupported TTS provider")

        if provider_id.startswith("azure-tts"):
            expected_v2 = provider_id == "azure-tts-v2"
            voices = [
                value
                for value in voice.get_all_azure_voices(filter_locals=None)
                if bool(voice.is_azure_v2_voice(value)) is expected_v2
            ]
            settings = []
            if expected_v2:
                settings = [
                    TTSSettingField(
                        name="speech_region",
                        label="Speech Region",
                        kind="text",
                        value=str(config.azure.get("speech_region", "") or ""),
                    ),
                    _password_field(
                        "speech_key",
                        "Speech Key",
                        bool(str(config.azure.get("speech_key", "") or "").strip()),
                    ),
                ]
        elif provider_id == "siliconflow":
            voices = voice.get_siliconflow_voices()
            settings = [
                _password_field(
                    "api_key",
                    "SiliconFlow API Key",
                    bool(str(config.siliconflow.get("api_key", "") or "").strip()),
                )
            ]
        elif provider_id == "gemini-tts":
            voices = voice.get_gemini_voices()
            settings = [
                _password_field(
                    "api_key",
                    "Gemini API Key",
                    bool(str(config.app.get("gemini_api_key", "") or "").strip()),
                )
            ]
        elif provider_id == "mimo-tts":
            voices = voice.get_mimo_voices()
            settings = [
                _password_field(
                    "api_key",
                    "MiMo API Key",
                    bool(str(config.app.get("mimo_api_key", "") or "").strip()),
                )
            ]
        elif provider_id == "minimax-tts":
            voices = voice.get_minimax_voices()
            settings = [
                _password_field(
                    "api_key",
                    "MiniMax TTS API Key",
                    bool(str(config.minimax_tts.get("api_key", "") or "").strip()),
                ),
                TTSSettingField(
                    name="base_url",
                    label="MiniMax TTS Endpoint",
                    kind="select",
                    value=voice.get_minimax_tts_endpoint(),
                    choices=[voice.MINIMAX_TTS_GLOBAL_URL, voice.MINIMAX_TTS_CN_URL],
                ),
                TTSSettingField(
                    name="model_id",
                    label="MiniMax TTS Model",
                    kind="select",
                    value=str(config.minimax_tts.get("model_id", voice.MINIMAX_TTS_DEFAULT_MODEL)),
                    choices=list(voice.MINIMAX_TTS_MODELS),
                ),
            ]
        elif provider_id == "elevenlabs":
            voices = []
            settings = [
                _password_field(
                    "api_key",
                    "ElevenLabs API Key",
                    bool(str(voice.get_elevenlabs_api_key() or "").strip()),
                ),
                TTSSettingField(
                    name="model_id",
                    label="ElevenLabs Model",
                    kind="select",
                    value=str(config.elevenlabs.get("model_id", "eleven_multilingual_v2")),
                    choices=["eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_v3"],
                ),
            ]
        else:
            voices = voice.get_chatterbox_voices()
            configured_voices = config.chatterbox.get("voices", []) or []
            settings = [
                TTSSettingField(
                    name="base_url",
                    label="Chatterbox Base URL",
                    kind="text",
                    value=str(config.chatterbox.get("base_url", "") or ""),
                ),
                _password_field(
                    "api_key",
                    "Chatterbox API Key",
                    bool(str(config.chatterbox.get("api_key", "") or "").strip()),
                ),
                TTSSettingField(
                    name="model_id",
                    label="Chatterbox Model",
                    kind="text",
                    value=str(config.chatterbox.get("model_id", "") or ""),
                ),
                TTSSettingField(
                    name="voices",
                    label="Chatterbox Voices",
                    kind="voice_list",
                    value=[str(item) for item in configured_voices],
                ),
            ]

        return TTSProviderMetadata(
            id=provider_id,
            label=providers[provider_id],
            voices=_voice_options(voices),
            settings=settings,
            requires_explicit_voice_refresh=provider_id in {"elevenlabs", "minimax-tts"},
        )
