from pathlib import Path

from app.services.music_batch.manager import MusicBatchManager
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
    manager.run_batch(_make_batch(tmp_path, parallel_jobs=4))

    assert assignments == {
        "a.mp3": 0,
        "b.mp3": 1,
        "c.mp3": 0,
        "d.mp3": 1,
    }


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
