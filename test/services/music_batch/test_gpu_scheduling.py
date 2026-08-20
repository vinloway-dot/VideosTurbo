from pathlib import Path

import pytest

from app.services.music_batch import gpu
from app.services.music_batch.gpu_manager import MusicBatchManager
from app.services.music_batch.models import BatchSettings, BatchState, SongItem


def _make_batch(tmp_path, *, parallel_jobs: int, encoder: str = "h264_nvenc"):
    songs = []
    for index, name in enumerate(["a.mp3", "b.mp3", "c.mp3", "d.mp3"]):
        source = tmp_path / name
        source.write_bytes(b"audio")
        songs.append(SongItem(source_path=str(source), added_index=index))
    return BatchState(
        batch_id="gpu-test",
        batch_dir=str(tmp_path / "batch"),
        settings=BatchSettings(
            output_root=str(tmp_path),
            video_script="Nature",
            video_keywords=["ocean"],
            stock_sources=[],
            video_encoder=encoder,
            parallel_jobs=parallel_jobs,
        ),
        songs=songs,
    )


def test_single_nvidia_gpu_is_used_for_all_parallel_jobs(tmp_path):
    assignments = {}

    def renderer(song, resolved, output_path):
        assignments[Path(song.source_path).name] = resolved.get("gpu_index")
        output_path.write_bytes(b"video")
        return output_path

    manager = MusicBatchManager(
        song_renderer=renderer,
        gpu_detector=lambda: [0],
    )
    state = manager.run_batch(_make_batch(tmp_path, parallel_jobs=2))

    assert assignments == {
        "a.mp3": 0,
        "b.mp3": 0,
        "c.mp3": 0,
        "d.mp3": 0,
    }
    assert all(song.status.value == "completed" for song in state.songs)
    assert {song.gpu_index for song in state.songs} == {0}


def test_multiple_nvidia_gpus_are_assigned_round_robin(tmp_path):
    assignments = {}

    def renderer(song, resolved, output_path):
        assignments[Path(song.source_path).name] = resolved.get("gpu_index")
        output_path.write_bytes(b"video")
        return output_path

    manager = MusicBatchManager(
        song_renderer=renderer,
        gpu_detector=lambda: [0, 1],
    )
    state = manager.run_batch(_make_batch(tmp_path, parallel_jobs=4))

    assert assignments == {
        "a.mp3": 0,
        "b.mp3": 1,
        "c.mp3": 0,
        "d.mp3": 1,
    }
    assert [song.gpu_index for song in state.songs] == [0, 1, 0, 1]


def test_non_nvenc_encoder_does_not_assign_nvidia_gpu(tmp_path):
    assignments = {}

    def renderer(song, resolved, output_path):
        assignments[Path(song.source_path).name] = resolved.get("gpu_index")
        output_path.write_bytes(b"video")
        return output_path

    manager = MusicBatchManager(
        song_renderer=renderer,
        gpu_detector=lambda: [0, 1],
    )
    manager.run_batch(
        _make_batch(tmp_path, parallel_jobs=2, encoder="libx264")
    )

    assert set(assignments.values()) == {None}


def test_detect_nvidia_gpu_indices_parses_nvidia_smi(monkeypatch):
    class Result:
        returncode = 0
        stdout = "0\n1\n"
        stderr = ""

    monkeypatch.setattr(gpu.shutil, "which", lambda _name: "nvidia-smi")
    monkeypatch.setattr(gpu.subprocess, "run", lambda *args, **kwargs: Result())

    assert gpu.detect_nvidia_gpu_indices() == [0, 1]


def test_nvenc_writer_injects_scheduled_gpu(monkeypatch):
    calls = []

    class Clip:
        def write_videofile(self, output_file, codec, **kwargs):
            calls.append((output_file, codec, kwargs))

    monkeypatch.setattr(
        gpu.video_service,
        "_get_effective_video_codec",
        lambda _codec=None: "h264_nvenc",
    )

    with gpu.nvenc_gpu_context(1):
        result = gpu._gpu_aware_write_videofile(
            Clip(),
            "out.mp4",
            "h264_nvenc",
            logger=None,
        )

    assert result == "h264_nvenc"
    assert calls[0][2]["ffmpeg_params"][-2:] == ["-gpu", "1"]


def test_music_batch_nvenc_writer_fails_closed_without_cpu_fallback(monkeypatch):
    fallback_calls = []

    class Clip:
        def write_videofile(self, output_file, codec, **kwargs):
            raise RuntimeError("nvenc failed")

    monkeypatch.setattr(
        gpu.video_service,
        "_get_effective_video_codec",
        lambda _codec=None: "h264_nvenc",
    )
    monkeypatch.setattr(
        gpu.video_service,
        "_fallback_write_videofile",
        lambda *args, **kwargs: fallback_calls.append((args, kwargs)),
    )

    with gpu.nvenc_gpu_context(1):
        with pytest.raises(RuntimeError, match="nvenc failed"):
            gpu._gpu_aware_write_videofile(
                Clip(),
                "out.mp4",
                "h264_nvenc",
                logger=None,
            )

    assert fallback_calls == []


def test_music_batch_nvenc_writer_fails_closed_even_without_detected_gpu(monkeypatch):
    fallback_calls = []

    class Clip:
        def write_videofile(self, output_file, codec, **kwargs):
            raise RuntimeError("nvenc failed")

    monkeypatch.setattr(
        gpu.video_service,
        "_get_effective_video_codec",
        lambda _codec=None: "h264_nvenc",
    )
    monkeypatch.setattr(
        gpu.video_service,
        "_fallback_write_videofile",
        lambda *args, **kwargs: fallback_calls.append((args, kwargs)),
    )

    with gpu.nvenc_gpu_context(None):
        with pytest.raises(RuntimeError, match="nvenc failed"):
            gpu._gpu_aware_write_videofile(
                Clip(),
                "out.mp4",
                "h264_nvenc",
                logger=None,
            )

    assert fallback_calls == []


def test_nvenc_concat_command_targets_scheduled_gpu(monkeypatch, tmp_path):
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr(gpu.utils, "get_ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    monkeypatch.setattr(
        gpu.video_service,
        "_get_effective_video_codec",
        lambda _codec=None: "h264_nvenc",
    )

    with gpu.nvenc_gpu_context(1):
        gpu._gpu_aware_concat(
            [str(tmp_path / "a.mp4")],
            str(tmp_path / "out.mp4"),
            threads=2,
            output_dir=str(tmp_path),
            max_duration=5,
        )

    command = commands[0]
    gpu_pos = command.index("-gpu")
    assert command[gpu_pos + 1] == "1"


def test_music_batch_nvenc_concat_fails_closed_without_libx264(monkeypatch, tmp_path):
    commands = []

    class Result:
        returncode = 1
        stdout = ""
        stderr = "nvenc session failed"

    def fake_run(command, **kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr(gpu.utils, "get_ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    monkeypatch.setattr(
        gpu.video_service,
        "_get_effective_video_codec",
        lambda _codec=None: "h264_nvenc",
    )

    with gpu.nvenc_gpu_context(0):
        with pytest.raises(RuntimeError, match="nvenc session failed"):
            gpu._gpu_aware_concat(
                [str(tmp_path / "a.mp4")],
                str(tmp_path / "out.mp4"),
                threads=2,
                output_dir=str(tmp_path),
            )

    codecs = [command[command.index("-c:v") + 1] for command in commands]
    assert codecs == ["h264_nvenc"]
