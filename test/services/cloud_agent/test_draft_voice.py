from pathlib import Path

from app.models.cloud_agent import CloudDraftVoiceRequest
from app.services.cloud_agent.draft_voice import DraftVoiceService


def test_prepared_voice_uses_full_script_once_then_reuses_matching_fingerprint(monkeypatch, tmp_path):
    calls = []

    def synthesize(*, text, voice_name, voice_rate, voice_file):
        calls.append((text, voice_name, voice_rate, Path(voice_file)))
        Path(voice_file).write_bytes(b"prepared mp3")
        return object()

    monkeypatch.setattr("app.services.cloud_agent.draft_voice.voice.tts", synthesize)
    service = DraftVoiceService(tmp_path / "draft-voices")
    request = CloudDraftVoiceRequest(
        script="The complete narration goes here.",
        tts_provider="elevenlabs",
        voice_id="elevenlabs:P9NVJuTccNIK9usP8iEI:001",
        voice_speed=1.0,
    )

    first = service.prepare(request)
    second = service.prepare(request)

    assert calls[0][:3] == (
        "The complete narration goes here.",
        "elevenlabs:P9NVJuTccNIK9usP8iEI:001",
        1.0,
    )
    assert first.reused is False
    assert second.reused is True
    assert first.fingerprint == second.fingerprint
    assert service.get(first.fingerprint).read_bytes() == b"prepared mp3"


def test_materialize_copies_only_the_server_managed_prepared_voice(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.cloud_agent.draft_voice.voice.tts",
        lambda **kwargs: Path(kwargs["voice_file"]).write_bytes(b"prepared mp3") or object(),
    )
    service = DraftVoiceService(tmp_path / "draft-voices")
    artifact = service.prepare(
        CloudDraftVoiceRequest(
            script="Narration.",
            tts_provider="elevenlabs",
            voice_id="elevenlabs:P9NVJuTccNIK9usP8iEI:001",
            voice_speed=1.0,
        )
    )
    destination = tmp_path / "job" / "audio" / "voice.mp3"

    assert service.materialize(artifact.fingerprint, destination) == destination
    assert destination.read_bytes() == b"prepared mp3"
