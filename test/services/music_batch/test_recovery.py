from pathlib import Path

from app.services.music_batch.models import BatchSettings, BatchState, BatchStatus, SongItem
from app.services.music_batch.recovery import (
    find_incomplete_batch_dirs,
    resume_incomplete_batches,
)
from app.services.music_batch.state import BatchStateStore


def _write_batch(root: Path, name: str, status: BatchStatus) -> Path:
    batch_dir = root / name
    batch_dir.mkdir(parents=True)
    song = root / f"{name}.mp3"
    song.write_bytes(b"audio")
    state = BatchState(
        batch_id=name,
        batch_dir=str(batch_dir),
        settings=BatchSettings(output_root=str(root)),
        songs=[SongItem(source_path=str(song), added_index=0)],
        status=status,
    )
    BatchStateStore(batch_dir).save(state)
    return batch_dir


def test_find_incomplete_batches_ignores_terminal_states(tmp_path):
    processing = _write_batch(tmp_path, "batch-processing", BatchStatus.processing)
    interrupted = _write_batch(tmp_path, "batch-interrupted", BatchStatus.interrupted)
    _write_batch(tmp_path, "batch-completed", BatchStatus.completed)
    _write_batch(tmp_path, "batch-failed", BatchStatus.failed)

    found = find_incomplete_batch_dirs(tmp_path)

    assert set(found) == {processing, interrupted}


def test_resume_incomplete_batches_runs_sequentially_and_continues_after_error(tmp_path):
    first = _write_batch(tmp_path, "batch-a", BatchStatus.processing)
    second = _write_batch(tmp_path, "batch-b", BatchStatus.interrupted)
    calls = []

    class Manager:
        def resume_batch(self, batch_dir):
            calls.append(Path(batch_dir))
            if len(calls) == 1:
                raise RuntimeError("first failed")
            return object()

    result = resume_incomplete_batches(tmp_path, manager_factory=Manager)

    assert calls == [first, second]
    assert result.resumed == [second]
    assert result.failed == [(first, "RuntimeError: first failed")]
