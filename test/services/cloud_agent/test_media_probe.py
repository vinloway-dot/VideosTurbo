import json
import subprocess
from pathlib import Path

import pytest

from app.services.cloud_agent.media_probe import (
    MediaProbe,
    probe_media,
    validate_audio,
    validate_video,
)
from app.services.cloud_agent.errors import MediaValidationError


def _write_media(tmp_path: Path, name: str = "media.bin", size: int = 128) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def _audio_payload(duration: float = 60.0):
    return {
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "duration": str(duration),
            }
        ],
        "format": {"duration": str(duration)},
    }


def _video_payload(duration: float = 10.0, width: int = 1080, height: int = 1920):
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": width,
                "height": height,
                "duration": str(duration),
            }
        ],
        "format": {"duration": str(duration)},
    }


def _final_payload(duration: float = 60.0, width: int = 1080, height: int = 1920):
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": width,
                "height": height,
                "duration": str(duration),
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "duration": str(duration),
            },
        ],
        "format": {"duration": str(duration)},
    }


def _patch_run(monkeypatch, payload=None, *, returncode=0, stderr=""):
    captured = {}

    def fake_run(command, *, capture_output, text, check):
        captured["command"] = command
        assert capture_output is True
        assert text is True
        assert check is False
        stdout = "" if payload is None else json.dumps(payload)
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr("app.services.cloud_agent.media_probe.subprocess.run", fake_run)
    return captured


def test_probe_media_normalizes_streams_duration_size_and_resolution(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "final.mp4", size=256)
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    ffmpeg = binary_dir / "ffmpeg"
    ffprobe = binary_dir / "ffprobe"
    ffmpeg.write_bytes(b"")
    ffprobe.write_bytes(b"")
    monkeypatch.setattr(
        "app.services.cloud_agent.media_probe.utils.get_ffmpeg_binary",
        lambda: str(ffmpeg),
    )
    captured = _patch_run(monkeypatch, _final_payload())

    result = probe_media(media_path)

    assert isinstance(result, MediaProbe)
    assert result.path == media_path
    assert result.size_bytes == 256
    assert result.duration == pytest.approx(60.0)
    assert result.has_video is True
    assert result.has_audio is True
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.width == 1080
    assert result.height == 1920
    assert captured["command"] == [
        str(ffprobe),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(media_path),
    ]


def test_probe_media_uses_path_ffprobe_when_ffmpeg_is_not_local(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path)
    monkeypatch.setattr(
        "app.services.cloud_agent.media_probe.utils.get_ffmpeg_binary", lambda: "ffmpeg"
    )
    monkeypatch.setattr(
        "app.services.cloud_agent.media_probe.shutil.which",
        lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None,
    )
    captured = _patch_run(monkeypatch, _audio_payload())

    probe_media(media_path)

    assert captured["command"][0] == "/usr/bin/ffprobe"


def test_validate_audio_accepts_audio_only_within_duration_policy(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "voice.mp3")
    _patch_run(monkeypatch, _audio_payload(duration=60.0))

    result = validate_audio(media_path, min_duration=58.0, max_duration=62.0)

    assert result.has_audio is True
    assert result.has_video is False
    assert result.duration == pytest.approx(60.0)


def test_validate_audio_rejects_missing_audio_stream(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "not-audio.mp4")
    _patch_run(monkeypatch, _video_payload())

    with pytest.raises(MediaValidationError, match="audio stream"):
        validate_audio(media_path, min_duration=1.0, max_duration=120.0)


def test_validate_video_accepts_video_only_flow_clip(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "clip_01.mp4")
    _patch_run(monkeypatch, _video_payload(duration=10.0))

    result = validate_video(
        media_path,
        min_size_bytes=64,
        min_duration=9.0,
        max_duration=11.0,
        expected_width=1080,
        expected_height=1920,
    )

    assert result.has_video is True
    assert result.has_audio is False


def test_validate_video_requires_audio_when_requested(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "final.mp4")
    _patch_run(monkeypatch, _video_payload(duration=60.0))

    with pytest.raises(MediaValidationError, match="audio stream"):
        validate_video(media_path, require_audio=True)


def test_validate_video_rejects_missing_video_stream(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "audio-only.mp3")
    _patch_run(monkeypatch, _audio_payload())

    with pytest.raises(MediaValidationError, match="video stream"):
        validate_video(media_path)


def test_probe_media_rejects_nonzero_ffprobe_and_redacts_url_query(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path)
    _patch_run(
        monkeypatch,
        returncode=1,
        stderr="failed to read https://example.com/video.mp4?token=super-secret&expires=1",
    )

    with pytest.raises(MediaValidationError) as exc_info:
        probe_media(media_path)

    message = str(exc_info.value)
    assert "ffprobe failed" in message
    assert "https://example.com/video.mp4" in message
    assert "super-secret" not in message
    assert "expires=1" not in message


def test_probe_media_rejects_invalid_json(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path)

    def fake_run(command, *, capture_output, text, check):
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    monkeypatch.setattr("app.services.cloud_agent.media_probe.subprocess.run", fake_run)

    with pytest.raises(MediaValidationError, match="invalid ffprobe JSON"):
        probe_media(media_path)


def test_validate_video_rejects_file_below_minimum_size(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "tiny.mp4", size=4)
    _patch_run(monkeypatch, _video_payload())

    with pytest.raises(MediaValidationError, match="minimum size"):
        validate_video(media_path, min_size_bytes=100)


def test_validate_audio_rejects_duration_outside_policy(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "voice.mp3")
    _patch_run(monkeypatch, _audio_payload(duration=40.0))

    with pytest.raises(MediaValidationError, match="duration"):
        validate_audio(media_path, min_duration=58.0, max_duration=62.0)


def test_validate_video_rejects_wrong_resolution(monkeypatch, tmp_path):
    media_path = _write_media(tmp_path, "final.mp4")
    _patch_run(monkeypatch, _final_payload(width=720, height=1280))

    with pytest.raises(MediaValidationError, match="resolution"):
        validate_video(
            media_path,
            require_audio=True,
            expected_width=1080,
            expected_height=1920,
        )
