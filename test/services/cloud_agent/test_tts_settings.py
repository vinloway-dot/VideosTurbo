from app.config import config
from app.services import voice
from app.services.cloud_agent import factory


def test_cloud_tts_settings_lists_exactly_eight_supported_providers():
    service = factory.build_cloud_tts_settings_service()

    assert [item.id for item in service.list_providers()] == [
        "azure-tts-v1",
        "azure-tts-v2",
        "siliconflow",
        "gemini-tts",
        "mimo-tts",
        "minimax-tts",
        "elevenlabs",
        "chatterbox",
    ]


def test_cloud_tts_settings_metadata_redacts_configured_secret(monkeypatch):
    monkeypatch.setitem(config.elevenlabs, "api_key", "do-not-return-me")
    service = factory.build_cloud_tts_settings_service()

    metadata = service.get_provider("elevenlabs").model_dump(mode="json")

    api_key = next(field for field in metadata["settings"] if field["name"] == "api_key")
    assert api_key["configured"] is True
    assert api_key["value"] is None
    assert "do-not-return-me" not in repr(metadata)


def test_cloud_tts_settings_splits_azure_catalog_by_existing_v2_rule(monkeypatch):
    monkeypatch.setattr(
        voice,
        "get_all_azure_voices",
        lambda **_kwargs: [
            "en-US-JennyNeural-Female",
            "en-US-AvaMultilingualNeural-V2-Female",
        ],
    )
    service = factory.build_cloud_tts_settings_service()

    assert [voice_option.id for voice_option in service.get_provider("azure-tts-v1").voices] == [
        "en-US-JennyNeural-Female"
    ]
    assert [voice_option.id for voice_option in service.get_provider("azure-tts-v2").voices] == [
        "en-US-AvaMultilingualNeural-V2-Female"
    ]
