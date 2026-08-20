from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from loguru import logger

from app.services.music_batch.models import BatchStatus
from app.services.music_batch.state import BatchStateStore

_TERMINAL_STATUSES = {
    BatchStatus.completed,
    BatchStatus.completed_with_failures,
    BatchStatus.failed,
}


class RecoveryManager(Protocol):
    def resume_batch(self, batch_dir: Path): ...


@dataclass
class RecoveryResult:
    resumed: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)


def find_incomplete_batch_dirs(output_root: Path) -> list[Path]:
    root = Path(output_root).expanduser().resolve()
    if not root.is_dir():
        return []

    batches: list[Path] = []
    for state_path in root.glob("*/batch_state.json"):
        batch_dir = state_path.parent
        try:
            state = BatchStateStore(batch_dir).load()
        except Exception as exc:
            logger.warning(f"skipping unreadable Music Batch state {state_path}: {exc}")
            continue
        if state.status not in _TERMINAL_STATUSES:
            batches.append(batch_dir)

    return sorted(
        batches,
        key=lambda path: (path / "batch_state.json").stat().st_mtime,
        reverse=True,
    )


def resume_incomplete_batches(
    output_root: Path,
    *,
    manager_factory: Callable[[], RecoveryManager] | None = None,
) -> RecoveryResult:
    if manager_factory is None:
        from app.services.music_batch.gpu_manager import MusicBatchManager

        manager_factory = MusicBatchManager

    result = RecoveryResult()
    for batch_dir in find_incomplete_batch_dirs(output_root):
        manager = manager_factory()
        try:
            manager.resume_batch(batch_dir)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            result.failed.append((batch_dir, message))
            logger.exception(f"Music Batch recovery failed for {batch_dir}: {exc}")
            continue
        result.resumed.append(batch_dir)
        logger.info(f"Music Batch recovery completed for {batch_dir}")
    return result
