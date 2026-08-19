from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from app.services.music_batch.models import SongItem, SortMode

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _is_supported_audio(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS


def discover_audio_files(folder: Path, include_subfolders: bool) -> list[Path]:
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return []

    iterator = folder.rglob("*") if include_subfolders else folder.iterdir()
    files = [path.resolve() for path in iterator if _is_supported_audio(path)]
    return sorted(files, key=lambda path: _natural_key(path.name))


def normalize_uploaded_paths(paths: Sequence[Path]) -> list[Path]:
    normalized: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(resolved)
    return normalized


def _natural_key(value: str) -> tuple[object, ...]:
    parts = re.split(r"(\d+)", value.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def sort_song_items(items: list[SongItem], mode: SortMode) -> list[SongItem]:
    if mode == SortMode.added:
        return sorted(items, key=lambda item: item.added_index)
    return sorted(
        items,
        key=lambda item: (_natural_key(Path(item.source_path).name), item.added_index),
    )


def _safe_stem(source_audio: Path) -> str:
    stem = _INVALID_FILENAME_CHARS.sub("_", source_audio.stem).strip().rstrip(".")
    return stem or "video"


def allocate_output_path(batch_dir: Path, source_audio: Path) -> Path:
    batch_dir = Path(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(Path(source_audio))
    candidate = batch_dir / f"{stem}.mp4"
    index = 2
    while candidate.exists():
        candidate = batch_dir / f"{stem}_{index}.mp4"
        index += 1
    return candidate
