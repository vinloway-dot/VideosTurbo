from pathlib import Path

from app.services.music_batch.manager import MusicBatchManager
from app.services.music_batch.models import (
    BatchSettings,
    BatchState,
    BatchStatus,
    SongItem,
    SongOverride,
    SongStatus,
)
from app.services.music_batch.sources import UsedClipRegistry, build_source_plan
from app.services.music_batch.state import BatchStateStore


class FakeRenderer:
    def __init__(self):
        self.calls = []

    def __call__(self, song, resolved, output_path):
        self.calls.append(
            {
                "song": Path(song.source_path).name,
                "keywords": list(resolved["video_keywords"]),
                "sources": list(resolved["stock_sources"]),
            }
        )
        output_path.write_bytes(b"video")
        return output_path


def _batch(tmp_path, names):
    songs = []
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(b"audio")
        songs.append(SongItem(source_path=str(path), added_index=index))
    batch_dir = tmp_path / "batch"
    return BatchState(
        batch_id="batch",
        batch_dir=str(batch_dir),
        settings=BatchSettings(
            output_root=str(tmp_path),
            video_script="global script",
            video_keywords=["ocean"],
            stock_sources=["pexels", "pixabay"],
        ),
        songs=songs,
    )


def test_two_song_batch_uses_global_and_override_settings(tmp_path):
    batch = _batch(tmp_path, ["001.mp3", "002.mp3"])
    batch.songs[1].override = SongOverride(video_keywords=["forest"])
    renderer = FakeRenderer()
    state = MusicBatchManager(song_renderer=renderer).run_batch(batch)
    assert [song.status for song in state.songs] == [
        SongStatus.completed,
        SongStatus.completed,
    ]
    assert renderer.calls[0]["keywords"] == ["ocean"]
    assert renderer.calls[1]["keywords"] == ["forest"]


def test_resume_skips_completed_and_restarts_interrupted_song(tmp_path):
    batch = _batch(tmp_path, ["001.mp3", "002.mp3", "003.mp3"])
    batch.status = BatchStatus.processing
    batch.songs[0].status = SongStatus.completed
    completed_output = Path(batch.batch_dir) / "001.mp4"
    completed_output.parent.mkdir(parents=True, exist_ok=True)
    completed_output.write_bytes(b"done")
    batch.songs[0].output_path = str(completed_output)
    batch.songs[1].status = SongStatus.processing
    store = BatchStateStore(Path(batch.batch_dir))
    store.save(batch)

    renderer = FakeRenderer()
    state = MusicBatchManager(song_renderer=renderer).resume_batch(Path(batch.batch_dir))
    assert [call["song"] for call in renderer.calls] == ["002.mp3", "003.mp3"]
    assert all(song.status == SongStatus.completed for song in state.songs)


def test_multi_source_plan_and_best_effort_duplicate_avoidance():
    plans = build_source_plan(
        ["pexels", "pixabay", "coverr"], ["ocean", "forest"], 180
    )
    assert {plan.provider for plan in plans} == {"pexels", "pixabay", "coverr"}

    registry = UsedClipRegistry()
    registry.mark("pexels", "old")
    assert registry.filter_candidates(
        "pexels",
        [("old", "cached.mp4"), ("new", "fresh.mp4")],
        avoid_reuse=True,
    ) == [("new", "fresh.mp4")]
    assert registry.filter_candidates(
        "pexels", [("old", "cached.mp4")], avoid_reuse=True
    ) == [("old", "cached.mp4")]
