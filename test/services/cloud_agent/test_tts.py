import importlib
import importlib.util
from pathlib import Path

import pytest

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobRecord,
    CloudJobStatus,
)
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.errors import MediaValidationError


def _tts_module():
    module_name = "app.services.cloud_agent.tts"
    assert importlib.util.find_spec(module_name) is not None, (
        "Task 7 Cloud Agent TTS adapter has not been implemented"
    )
    return importlib.import_module(module_name)


def _job(**changes) -> CloudJobRecord:
    values = {
        "subject": "Why Saturn Has a Hexagon",
        "script": "A valid narration script.",
        "master_prompt": "Create six videos from this narration.",
        "clip_plan": empty_six_clip_plan(target_words=130),
        "language": "English",
        "target_words": 130,
        "tts_provider": "azure-tts-v1",
        "voice_id": "en-US-JennyNeural-Female",
        "voice_speed": 1.0,
        "id": "job-123",
        "status": CloudJobStatus.TTS_GENERATING,
        "checkpoint": CloudJobCheckpoint.PREFLIGHT_PASSED,
        "control_request": CloudControlRequest.NONE,
        "current_step": "tts_generating",
        "progress": 15,
        "flow_status": "",
        "canva_status": "",
        "voice_file": "",
        "final_video": "",
        "error_code": "",
        "error_message": "",
        "worker_id": "worker-a",
        "lease_until": "2026-08-23T00:30:00+00:00",
        "created_at": "2026-08-23T00:00:00+00:00",
        "started_at": "2026-08-23T00:00:01+00:00",
        "completed_at": "",
        "updated_at": "2026-08-23T00:00:02+00:00",
    }
    values.update(changes)
    return CloudJobRecord(**values)


def test_existing_voice_tts_client_calls_existing_router_with_job_values(
    monkeypatch, tmp_path
):
    module = _tts_module()
    calls = []
    output_path = tmp_path / "audio" / "voice.mp3"

    def fake_tts(**kwargs):
        calls.append(kwargs)
        Path(kwargs["voice_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["voice_file"]).write_bytes(b"audio-bytes")
        return object()

    monkeypatch.setattr(module.voice, "tts", fake_tts)
    job = _job(voice_speed=1.15)

    result = module.ExistingVoiceTTSClient().generate(job, output_path)

    assert result == output_path
    assert calls == [
        {
            "text": job.script,
            "voice_name": job.voice_id,
            "voice_rate": 1.15,
            "voice_file": str(output_path),
        }
    ]


def test_existing_voice_tts_client_rejects_known_provider_voice_mismatch(
    monkeypatch, tmp_path
):
    module = _tts_module()
    called = False

    def fake_tts(**_kwargs):
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(module.voice, "tts", fake_tts)
    job = _job(tts_provider="elevenlabs", voice_id="gemini:Zephyr-Bright")

    with pytest.raises(ValueError, match="tts_provider.*voice_id|voice_id.*tts_provider"):
        module.ExistingVoiceTTSClient().generate(job, tmp_path / "voice.mp3")

    assert called is False


def test_existing_voice_tts_client_wraps_router_exception(monkeypatch, tmp_path):
    module = _tts_module()

    def fake_tts(**_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(module.voice, "tts", fake_tts)

    with pytest.raises(MediaValidationError, match="TTS generation failed"):
        module.ExistingVoiceTTSClient().generate(_job(), tmp_path / "voice.mp3")


def test_existing_voice_tts_client_rejects_unsuccessful_router_result(
    monkeypatch, tmp_path
):
    module = _tts_module()
    monkeypatch.setattr(module.voice, "tts", lambda **_kwargs: None)

    with pytest.raises(MediaValidationError, match="TTS generation failed"):
        module.ExistingVoiceTTSClient().generate(_job(), tmp_path / "voice.mp3")


def test_existing_voice_tts_client_requires_created_artifact(monkeypatch, tmp_path):
    module = _tts_module()
    monkeypatch.setattr(module.voice, "tts", lambda **_kwargs: object())

    with pytest.raises(MediaValidationError, match="did not produce.*voice.mp3"):
        module.ExistingVoiceTTSClient().generate(_job(), tmp_path / "voice.mp3")


def test_existing_voice_tts_client_rejects_empty_artifact(monkeypatch, tmp_path):
    module = _tts_module()
    output_path = tmp_path / "voice.mp3"

    def fake_tts(**_kwargs):
        output_path.write_bytes(b"")
        return object()

    monkeypatch.setattr(module.voice, "tts", fake_tts)

    with pytest.raises(MediaValidationError, match="empty"):
        module.ExistingVoiceTTSClient().generate(_job(), output_path)


def test_existing_voice_tts_client_does_not_apply_legacy_duration_ceiling(
    monkeypatch, tmp_path
):
    module = _tts_module()
    output_path = tmp_path / "voice.mp3"

    def fake_tts(**_kwargs):
        output_path.write_bytes(b"valid-production-audio")
        return object()

    monkeypatch.setattr(module.voice, "tts", fake_tts)
    monkeypatch.setattr(module.voice, "get_audio_duration", lambda _path: 63.25)

    result = module.ExistingVoiceTTSClient().generate(_job(), output_path)

    assert result == output_path
    assert output_path.read_bytes() == b"valid-production-audio"
