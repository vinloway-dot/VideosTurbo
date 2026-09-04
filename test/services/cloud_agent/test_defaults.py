from types import SimpleNamespace

from app.config import config
from app.services.cloud_agent import factory


def test_cloud_defaults_persist_voice_provider_speed_and_custom_system_prompt(monkeypatch):
    saved = []
    monkeypatch.setattr(config, "save_config", lambda: saved.append(True))
    service = factory.build_cloud_agent_defaults_service()

    result = service.update(
        SimpleNamespace(
            tts_provider="elevenlabs",
            voice_id="elevenlabs:P9NVJuTccNIK9usP8iEI:001",
            voice_speed=1.1,
            custom_system_prompt="Write in a calm documentary tone.",
            create_canva_captions=True,
        )
    )

    assert result.tts_provider == "elevenlabs"
    assert result.voice_id == "elevenlabs:P9NVJuTccNIK9usP8iEI:001"
    assert result.voice_speed == 1.1
    assert result.custom_system_prompt == "Write in a calm documentary tone."
    assert result.create_canva_captions is True
    assert saved == [True]


def test_cloud_defaults_reset_restores_the_system_defaults(monkeypatch):
    monkeypatch.setitem(config.app, "cloud_agent_default_tts_provider", "elevenlabs")
    monkeypatch.setitem(config.app, "cloud_agent_default_voice_id", "elevenlabs:voice:Name")
    monkeypatch.setitem(config.app, "cloud_agent_default_voice_speed", 1.1)
    monkeypatch.setitem(config.app, "cloud_agent_default_custom_system_prompt", "Custom")
    monkeypatch.setitem(config.app, "cloud_agent_default_create_canva_captions", True)
    monkeypatch.setattr(config, "save_config", lambda: None)
    service = factory.build_cloud_agent_defaults_service()

    result = service.reset()

    assert result.tts_provider == "azure-tts-v1"
    assert result.voice_id == ""
    assert result.voice_speed == 1.0
    assert result.custom_system_prompt == ""
    assert result.create_canva_captions is False
