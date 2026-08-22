from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import voice


WEBUI_SOURCE = Path("webui/Main.py").read_text(encoding="utf-8")


def test_elevenlabs_voice_catalog_uses_current_v2_api_without_removed_favorite_filter():
    def fake_get(url, params, headers, timeout):
        assert url == "https://api.elevenlabs.io/v2/voices"
        assert headers == {"xi-api-key": "fake-api-key"}
        assert timeout == 10
        if "is_favorite" in params:
            return SimpleNamespace(
                status_code=422,
                text="Unknown query parameter: is_favorite",
                json=lambda: {},
            )
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "voices": [
                    {
                        "voice_id": "default123",
                        "name": "Rachel",
                        "status": "enabled",
                    }
                ]
            },
        )

    with patch("app.services.voice.requests.get", side_effect=fake_get):
        voices = voice.get_elevenlabs_voices("fake-api-key")

    assert voices == ["elevenlabs:default123:Rachel"]


def test_elevenlabs_voice_catalog_can_surface_http_error_to_webui():
    response = SimpleNamespace(
        status_code=403,
        text="missing voices_read permission",
        json=lambda: {},
    )

    with patch("app.services.voice.requests.get", return_value=response):
        with pytest.raises(voice.ElevenLabsVoiceCatalogError, match="403"):
            voice.get_elevenlabs_voices("fake-api-key", raise_on_error=True)


def test_webui_can_refresh_elevenlabs_voices_and_does_not_cache_empty_results():
    assert 'key="refresh_elevenlabs_voices_button"' in WEBUI_SOURCE
    assert "raise_on_error=True" in WEBUI_SOURCE
    assert "if loaded_elevenlabs_voices:" in WEBUI_SOURCE
    assert "st.session_state[cache_key] = loaded_elevenlabs_voices" in WEBUI_SOURCE
