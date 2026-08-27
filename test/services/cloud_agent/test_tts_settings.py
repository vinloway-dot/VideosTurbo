from types import SimpleNamespace

import pytest

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


def test_blank_secret_preserves_value_but_explicit_clear_removes_it(monkeypatch):
    monkeypatch.setitem(config.elevenlabs, "api_key", "stored-secret")
    monkeypatch.setattr(config, "save_config", lambda: None)
    service = factory.build_cloud_tts_settings_service()

    service.update_provider(
        "elevenlabs",
        SimpleNamespace(settings={"api_key": ""}, clear_secret_fields=[]),
    )
    assert config.elevenlabs["api_key"] == "stored-secret"

    service.update_provider(
        "elevenlabs",
        SimpleNamespace(settings={}, clear_secret_fields=["api_key"]),
    )
    assert config.elevenlabs.get("api_key", "") == ""


def test_settings_reject_unrelated_field_and_non_secret_clear():
    service = factory.build_cloud_tts_settings_service()

    with pytest.raises(ValueError, match="not supported"):
        service.update_provider(
            "gemini-tts",
            SimpleNamespace(settings={"base_url": "x"}, clear_secret_fields=[]),
        )
    with pytest.raises(ValueError, match="explicit"):
        service.update_provider(
            "elevenlabs",
            SimpleNamespace(settings={}, clear_secret_fields=["model_id"]),
        )


def test_refresh_elevenlabs_uses_key_without_returning_it(monkeypatch):
    monkeypatch.setitem(config.elevenlabs, "api_key", "configured-secret")
    monkeypatch.setattr(
        voice,
        "get_elevenlabs_voices",
        lambda api_key: ["elevenlabs:id:Name"],
    )
    service = factory.build_cloud_tts_settings_service()

    result = service.refresh_voices("elevenlabs")

    assert [item.id for item in result.voices] == ["elevenlabs:id:Name"]
    assert "configured-secret" not in repr(result.model_dump())


def test_refresh_minimax_maps_existing_catalog_to_router_voice_ids(monkeypatch):
    monkeypatch.setattr(
        voice,
        "get_minimax_voice_catalog",
        lambda: [{"voice_id": "narrator", "voice_name": "Narrator"}],
    )
    service = factory.build_cloud_tts_settings_service()

    result = service.refresh_voices("minimax-tts")

    assert [(item.id, item.label) for item in result.voices] == [
        ("minimax:narrator", "minimax:narrator")
    ]


def test_chatterbox_voices_are_normalized_before_being_returned(monkeypatch):
    monkeypatch.setitem(config.chatterbox, "voices", "calm, bright")
    service = factory.build_cloud_tts_settings_service()

    result = service.get_provider("chatterbox")

    assert [item.id for item in result.voices] == [
        "chatterbox:calm",
        "chatterbox:bright",
    ]
    voices_field = next(field for field in result.settings if field.name == "voices")
    assert voices_field.value == ["calm", "bright"]
