from types import SimpleNamespace
from unittest.mock import patch

from app.services import voice


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
