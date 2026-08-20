from pathlib import Path

from app.services.music_batch import concat
from app.services.music_batch.concat import (
    MediaSignature,
    are_stream_copy_compatible,
)


def test_compatibility_requires_matching_video_and_audio_signatures(monkeypatch):
    signatures = {
        "a.mp4": MediaSignature(
            video_codec="h264",
            width=1920,
            height=1080,
            frame_rate="30/1",
            audio_codec="aac",
            sample_rate=48000,
            channel_layout="stereo",
        ),
        "b.mp4": MediaSignature(
            video_codec="h264",
            width=1920,
            height=1080,
            frame_rate="30/1",
            audio_codec="aac",
            sample_rate=48000,
            channel_layout="stereo",
        ),
    }
    monkeypatch.setattr(
        concat, "probe_media_signature", lambda path: signatures[path.name]
    )
    ok, reason = are_stream_copy_compatible([Path("a.mp4"), Path("b.mp4")])
    assert ok is True
    assert reason == "compatible"


def test_compatibility_returns_concrete_mismatch_reason(monkeypatch):
    signatures = {
        "a.mp4": MediaSignature("h264", 1920, 1080, "30/1", "aac", 48000, "stereo"),
        "b.mp4": MediaSignature("h264", 1280, 720, "30/1", "aac", 48000, "stereo"),
    }
    monkeypatch.setattr(
        concat, "probe_media_signature", lambda path: signatures[path.name]
    )
    ok, reason = are_stream_copy_compatible([Path("a.mp4"), Path("b.mp4")])
    assert ok is False
    assert "resolution" in reason


def test_probe_media_signature_parses_ffprobe_json(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stderr = ""
        stdout = """{
          "streams": [
            {"codec_type":"video","codec_name":"h264","width":1920,"height":1080,"avg_frame_rate":"30/1"},
            {"codec_type":"audio","codec_name":"aac","sample_rate":"48000","channel_layout":"stereo"}
          ]
        }"""

    monkeypatch.setattr(concat.subprocess, "run", lambda *args, **kwargs: Result())
    monkeypatch.setattr(concat, "_ffprobe_executable", lambda: "ffprobe")
    signature = concat.probe_media_signature(tmp_path / "video.mp4")
    assert signature.width == 1920
    assert signature.audio_codec == "aac"
    assert signature.sample_rate == 48000


def test_concat_stream_copy_uses_copy_codec(monkeypatch, tmp_path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    output = tmp_path / "full.mp4"
    captured = {}

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        output.write_bytes(b"done")
        return Result()

    monkeypatch.setattr(concat, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(concat.subprocess, "run", fake_run)
    assert concat.concat_stream_copy([first, second], output) == output
    command = captured["command"]
    assert "-c" in command
    assert "copy" in command


def test_concat_reencode_uses_requested_video_codec(monkeypatch, tmp_path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    output = tmp_path / "full.mp4"
    captured = {}

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        output.write_bytes(b"done")
        return Result()

    monkeypatch.setattr(concat, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(
        concat,
        "probe_media_signature",
        lambda path: MediaSignature("h264", 1920, 1080, "30/1", "aac", 48000, "stereo"),
    )
    monkeypatch.setattr(concat.subprocess, "run", fake_run)
    concat.concat_reencode([first, second], output, "h264_nvenc")
    assert "h264_nvenc" in captured["command"]
