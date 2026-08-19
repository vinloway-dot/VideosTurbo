from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from app.services.music_batch.models import BatchState, BatchStatus, SongStatus


class BatchStateStore:
    def __init__(self, batch_dir: Path):
        self.batch_dir = Path(batch_dir)
        self.state_path = self.batch_dir / "batch_state.json"
        self.temp_path = self.batch_dir / "batch_state.json.tmp"
        self._lock = threading.RLock()

    def save(self, state: BatchState) -> BatchState:
        with self._lock:
            self.batch_dir.mkdir(parents=True, exist_ok=True)
            payload = state.model_dump_json(indent=2)
            with self.temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(self.temp_path, self.state_path)
            return state

    def load(self) -> BatchState:
        with self._lock:
            raw = self.state_path.read_text(encoding="utf-8")
            return BatchState.model_validate_json(raw)

    def mutate(self, fn: Callable[[BatchState], BatchState | None]) -> BatchState:
        with self._lock:
            state = self.load()
            updated = fn(state)
            if updated is not None:
                state = updated
            self.save(state)
            return state

    def recover_interrupted(self) -> BatchState:
        def recover(state: BatchState) -> BatchState:
            for song in state.songs:
                if song.status in {SongStatus.processing, SongStatus.retrying}:
                    song.status = SongStatus.pending
                    song.started_at = None
            if state.status not in {
                BatchStatus.completed,
                BatchStatus.completed_with_failures,
                BatchStatus.failed,
            }:
                state.status = BatchStatus.interrupted
            return state

        return self.mutate(recover)

    def retry_failed(self) -> BatchState:
        def reset(state: BatchState) -> BatchState:
            for song in state.songs:
                if song.status == SongStatus.failed:
                    song.status = SongStatus.pending
                    song.attempts = 0
                    song.latest_error = None
                    song.started_at = None
                    song.completed_at = None
            if any(song.status == SongStatus.pending for song in state.songs):
                state.status = BatchStatus.interrupted
                state.fatal_error = None
            return state

        return self.mutate(reset)


def make_restart_directory(previous: Path) -> Path:
    previous = Path(previous)
    index = 1
    while True:
        candidate = previous.with_name(f"{previous.name}_restart_{index:02d}")
        if not candidate.exists():
            return candidate
        index += 1
