from app.services.music_batch.models import (
    BatchSettings,
    BatchState,
    BatchStatus,
    SongItem,
    SongOverride,
    SongStatus,
    SortMode,
    resolve_song_settings,
)


def test_batch_defaults_match_spec():
    cfg = BatchSettings(output_root="D:/out")
    assert cfg.video_aspect == "16:9"
    assert cfg.retry_count == 2
    assert cfg.parallel_jobs == 1
    assert cfg.avoid_reusing_clips is False
    assert cfg.sort_mode == SortMode.filename


def test_song_override_replaces_only_explicit_fields():
    cfg = BatchSettings(
        output_root="D:/out",
        video_script="global script",
        video_keywords=["ocean"],
        stock_sources=["pexels", "pixabay"],
        video_clip_duration=8,
    )
    song = SongItem(
        source_path="D:/music/a.mp3",
        added_index=0,
        override=SongOverride(video_keywords=["forest"], video_clip_duration=10),
    )
    resolved = resolve_song_settings(cfg, song)
    assert resolved["video_script"] == "global script"
    assert resolved["video_keywords"] == ["forest"]
    assert resolved["stock_sources"] == ["pexels", "pixabay"]
    assert resolved["video_clip_duration"] == 10


def test_override_does_not_replace_globals_with_none():
    cfg = BatchSettings(
        output_root="D:/out",
        video_script="global script",
        video_keywords=["ocean"],
    )
    song = SongItem(
        source_path="D:/music/a.mp3",
        added_index=0,
        override=SongOverride(video_script=None, video_keywords=None),
    )
    resolved = resolve_song_settings(cfg, song)
    assert resolved["video_script"] == "global script"
    assert resolved["video_keywords"] == ["ocean"]


def test_batch_state_test_factory_has_requested_statuses():
    state = BatchState.new_for_test(
        status=BatchStatus.processing,
        song_statuses=[SongStatus.completed, SongStatus.failed],
    )
    assert state.status == BatchStatus.processing
    assert [song.status for song in state.songs] == [
        SongStatus.completed,
        SongStatus.failed,
    ]
