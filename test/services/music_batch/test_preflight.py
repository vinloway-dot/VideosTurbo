from pathlib import Path

from app.services.music_batch import preflight
from app.services.music_batch.models import BatchSettings, SongItem, SongOverride
from app.services.music_batch.preflight import run_preflight


def test_preflight_requires_song(tmp_path):
    issues = run_preflight(BatchSettings(output_root=str(tmp_path)), [])
    assert any(i.code == "no_inputs" and i.level == "error" for i in issues)


def test_nvenc_failure_is_explicit(monkeypatch, tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(
        preflight, "probe_encoder", lambda codec: (False, "nvenc unavailable")
    )
    monkeypatch.setattr(preflight, "_ffmpeg_executable", lambda: "ffmpeg")
    settings = BatchSettings(
        output_root=str(tmp_path),
        video_encoder="h264_nvenc",
        stock_sources=[],
    )
    issues = run_preflight(settings, [SongItem(source_path=str(song), added_index=0)])
    assert any(
        i.code == "encoder_unavailable" and "h264_nvenc" in i.message
        for i in issues
    )


def test_missing_provider_key_is_reported(monkeypatch, tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(preflight, "probe_encoder", lambda codec: (True, "ok"))
    monkeypatch.setattr(preflight, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(preflight.config, "app", {"pexels_api_keys": []})
    settings = BatchSettings(
        output_root=str(tmp_path),
        stock_sources=["pexels"],
    )
    issues = run_preflight(settings, [SongItem(source_path=str(song), added_index=0)])
    assert any(i.code == "missing_provider_key" for i in issues)


def test_provider_key_required_when_only_song_override_uses_provider(
    monkeypatch, tmp_path
):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(preflight, "probe_encoder", lambda codec: (True, "ok"))
    monkeypatch.setattr(preflight, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(
        preflight.config,
        "app",
        {"pexels_api_keys": ["configured"], "coverr_api_keys": []},
    )
    settings = BatchSettings(
        output_root=str(tmp_path),
        stock_sources=["pexels"],
    )
    item = SongItem(
        source_path=str(song),
        added_index=0,
        override=SongOverride(stock_sources=["coverr"]),
    )
    issues = run_preflight(settings, [item])
    assert any(
        issue.code == "missing_provider_key" and "coverr" in issue.message
        for issue in issues
    )


def test_unreadable_or_missing_song_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "probe_encoder", lambda codec: (True, "ok"))
    monkeypatch.setattr(preflight, "_ffmpeg_executable", lambda: "ffmpeg")
    settings = BatchSettings(output_root=str(tmp_path), stock_sources=[])
    missing = tmp_path / "missing.mp3"
    issues = run_preflight(
        settings, [SongItem(source_path=str(missing), added_index=0)]
    )
    assert any(i.code == "input_unreadable" for i in issues)


def test_probe_encoder_returns_detail_on_process_failure(monkeypatch):
    class Result:
        returncode = 1
        stderr = "encoder failed"
        stdout = ""

    monkeypatch.setattr(preflight, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: Result())
    ok, detail = preflight.probe_encoder("h264_nvenc")
    assert ok is False
    assert "encoder failed" in detail
