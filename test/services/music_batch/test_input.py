from pathlib import Path

from app.services.music_batch.input import (
    allocate_output_path,
    discover_audio_files,
    normalize_uploaded_paths,
    sort_song_items,
)
from app.services.music_batch.models import SongItem, SortMode


def test_discover_audio_files_respects_subfolder_flag(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "ignore.txt").write_text("x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.wav").write_bytes(b"x")

    assert [p.name for p in discover_audio_files(tmp_path, False)] == ["a.mp3"]
    assert {p.name for p in discover_audio_files(tmp_path, True)} == {"a.mp3", "b.wav"}


def test_discover_audio_files_is_case_insensitive(tmp_path):
    (tmp_path / "A.MP3").write_bytes(b"x")
    (tmp_path / "B.FlAc").write_bytes(b"x")
    assert {p.name for p in discover_audio_files(tmp_path, False)} == {"A.MP3", "B.FlAc"}


def test_filename_sort_is_natural():
    items = [
        SongItem(source_path="10.mp3", added_index=0),
        SongItem(source_path="2.mp3", added_index=1),
    ]
    ordered = sort_song_items(items, SortMode.filename)
    assert [Path(x.source_path).name for x in ordered] == ["2.mp3", "10.mp3"]


def test_added_sort_preserves_added_index():
    items = [
        SongItem(source_path="b.mp3", added_index=2),
        SongItem(source_path="a.mp3", added_index=0),
        SongItem(source_path="c.mp3", added_index=1),
    ]
    ordered = sort_song_items(items, SortMode.added)
    assert [x.added_index for x in ordered] == [0, 1, 2]


def test_normalize_uploaded_paths_deduplicates_stably(tmp_path):
    first = tmp_path / "a.mp3"
    second = tmp_path / "b.wav"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    result = normalize_uploaded_paths([first, first, second])
    assert result == [first.resolve(), second.resolve()]


def test_allocate_output_path_never_overwrites(tmp_path):
    first = allocate_output_path(tmp_path, Path("Calm Ocean.mp3"))
    first.touch()
    second = allocate_output_path(tmp_path, Path("Calm Ocean.mp3"))
    assert second.name == "Calm Ocean_2.mp4"
