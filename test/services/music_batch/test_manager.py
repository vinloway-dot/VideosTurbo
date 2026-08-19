import json
from pathlib import Path

from app.services.music_batch import manager as manager_module
from app.services.music_batch.manager import MusicBatchManager
from app.services.music_batch.models import (
    BatchSettings,
    BatchState,
    SongItem,
    SongStatus,
    resolve_song_settings,
)


def make_batch(tmp_path, names, retry_count=2, parallel_jobs=1, combine_all=False):
    songs = []
    for index, name in enumerate(names):
        source = tmp_path / name
        source.write_bytes(b"audio")
        songs.append(SongItem(source_path=str(source), added_index=index))
    batch_dir = tmp_path / "batch"
    return BatchState(
        batch_id="batch-test",
        batch_dir=str(batch_dir),
        settings=BatchSettings(
            output_root=str(tmp_path),
            video_script="Peaceful nature",
            video_keywords=["ocean", "forest"],
            stock_sources=["pexels"],
            retry_count=retry_count,
            parallel_jobs=parallel_jobs,
            combine_all=combine_all,
        ),
        songs=songs,
    )


def test_render_song_builds_existing_video_request(tmp_path):
    captured = {}

    def adapter(params, out):
        captured["params"] = params
        out.write_bytes(b"video")
        return out

    manager = MusicBatchManager(render_adapter=adapter)
    song_path = tmp_path / "song.mp3"
    song_path.write_bytes(b"audio")
    song = SongItem(source_path=str(song_path), added_index=0)
    settings = BatchSettings(
        output_root=str(tmp_path),
        video_script="Peaceful nature",
        video_keywords=["ocean", "forest"],
        stock_sources=["pexels"],
        video_encoder="h264_nvenc",
    )
    output = tmp_path / "song.mp4"
    manager.render_song(song, resolve_song_settings(settings, song), output)
    assert captured["params"]["custom_audio_file"].endswith("song.mp3")
    assert captured["params"]["video_script"] == "Peaceful nature"
    assert captured["params"]["subtitle_enabled"] is False
    assert captured["params"]["bgm_type"] == ""
    assert captured["params"]["video_encoder"] == "h264_nvenc"


def test_failed_song_retries_then_continues(tmp_path):
    calls = {"a": 0, "b": 0}

    def renderer(song, _resolved, output_path):
        name = Path(song.source_path).stem
        calls[name] += 1
        if name == "a":
            raise RuntimeError("render failed")
        output_path.write_bytes(b"video")
        return output_path

    manager = MusicBatchManager(song_renderer=renderer)
    state = manager.run_batch(make_batch(tmp_path, ["a.mp3", "b.mp3"], retry_count=2))
    assert calls["a"] == 3
    assert calls["b"] == 1
    assert state.songs[0].status == SongStatus.failed
    assert state.songs[1].status == SongStatus.completed


def test_run_batch_writes_machine_and_human_reports(tmp_path):
    def renderer(_song, _resolved, output_path):
        output_path.write_bytes(b"video")
        return output_path

    state = MusicBatchManager(song_renderer=renderer).run_batch(
        make_batch(tmp_path, ["a.mp3"])
    )
    batch_dir = Path(state.batch_dir)
    report_json = batch_dir / "batch_report.json"
    report_txt = batch_dir / "batch_report.txt"
    assert report_json.exists()
    assert report_txt.exists()
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["completed_count"] == 1
    assert payload["failed_count"] == 0
    assert "Completed: 1" in report_txt.read_text(encoding="utf-8")


def test_incompatible_combine_requires_confirmation(monkeypatch, tmp_path):
    def renderer(_song, _resolved, output_path):
        output_path.write_bytes(b"video")
        return output_path

    monkeypatch.setattr(
        manager_module,
        "are_stream_copy_compatible",
        lambda paths: (False, "resolution mismatch"),
    )
    stream_copy_called = []
    monkeypatch.setattr(
        manager_module,
        "concat_stream_copy",
        lambda *args, **kwargs: stream_copy_called.append(True),
    )
    state = MusicBatchManager(song_renderer=renderer).run_batch(
        make_batch(tmp_path, ["a.mp3", "b.mp3"], combine_all=True)
    )
    assert state.status.value == "needs_reencode_confirmation"
    assert state.compilation_status == "needs_reencode_confirmation"
    assert "resolution mismatch" in (state.compilation_error or "")
    assert stream_copy_called == []


def test_compatible_combine_uses_stream_copy(monkeypatch, tmp_path):
    def renderer(_song, _resolved, output_path):
        output_path.write_bytes(b"video")
        return output_path

    monkeypatch.setattr(
        manager_module, "are_stream_copy_compatible", lambda paths: (True, "compatible")
    )

    def fake_concat(paths, output):
        output.write_bytes(b"full")
        return output

    monkeypatch.setattr(manager_module, "concat_stream_copy", fake_concat)
    state = MusicBatchManager(song_renderer=renderer).run_batch(
        make_batch(tmp_path, ["a.mp3", "b.mp3"], combine_all=True)
    )
    assert state.compilation_status == "completed"
    assert Path(state.compilation_path).name == "Full_Compilation.mp4"


def test_parallel_jobs_complete_without_corrupting_state(tmp_path):
    def renderer(_song, _resolved, output_path):
        output_path.write_bytes(b"video")
        return output_path

    state = MusicBatchManager(song_renderer=renderer).run_batch(
        make_batch(
            tmp_path,
            ["a.mp3", "b.mp3", "c.mp3", "d.mp3"],
            parallel_jobs=2,
        )
    )
    assert all(song.status == SongStatus.completed for song in state.songs)
    loaded = json.loads(
        (Path(state.batch_dir) / "batch_state.json").read_text(encoding="utf-8")
    )
    assert len(loaded["songs"]) == 4
