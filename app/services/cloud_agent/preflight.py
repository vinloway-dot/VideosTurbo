from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from app.models.cloud_agent import SessionCheckResult
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.storage import CloudJobStorage


class SessionReadiness(Protocol):
    def ensure_all_ready(self, job_id: str) -> dict[str, SessionCheckResult]: ...


@dataclass(frozen=True)
class PreflightResult:
    worker_id: str
    storage_writable: bool
    free_space_bytes: int
    sessions: Mapping[str, SessionCheckResult]


def _probe_storage_writable(root: Path) -> bool:
    """Verify actual create/write/delete access instead of trusting os.access()."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".cloud-agent-preflight-",
            dir=root,
            delete=False,
        ) as handle:
            probe_path = Path(handle.name)
            handle.write(b"ok")
            handle.flush()
        probe_path.unlink()
        return True
    except OSError:
        return False


class PreflightManager:
    def __init__(
        self,
        store: CloudJobStore,
        storage: CloudJobStorage,
        sessions: SessionReadiness,
        *,
        min_free_disk_gb: float,
        storage_writable_probe: Callable[[Path], bool] = _probe_storage_writable,
        disk_usage: Callable[[Path], object] = shutil.disk_usage,
    ) -> None:
        if min_free_disk_gb < 0:
            raise ValueError("min_free_disk_gb must be non-negative")
        self.store = store
        self.storage = storage
        self.sessions = sessions
        self.min_free_disk_gb = float(min_free_disk_gb)
        self.storage_writable_probe = storage_writable_probe
        self.disk_usage = disk_usage

    def ensure_ready(self, job_id: str, *, worker_id: str) -> PreflightResult:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            raise RuntimeError("worker identity is required for preflight")

        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"cloud job not found: {job_id}")
        if job.worker_id != normalized_worker_id:
            raise RuntimeError(
                f"worker ownership mismatch for job {job_id}: "
                f"expected {job.worker_id or '<unclaimed>'}, got {normalized_worker_id}"
            )

        last_seen = self.store.get_worker_last_seen(normalized_worker_id)
        if not last_seen:
            raise RuntimeError(
                f"worker heartbeat is unavailable for {normalized_worker_id}"
            )

        storage_root = Path(self.storage.root)
        if not self.storage_writable_probe(storage_root):
            raise RuntimeError(f"cloud agent storage is not writable: {storage_root}")

        usage = self.disk_usage(storage_root)
        free_space_bytes = int(getattr(usage, "free"))
        required_bytes = int(self.min_free_disk_gb * 1024**3)
        if free_space_bytes < required_bytes:
            raise RuntimeError(
                "insufficient free disk space for cloud agent: "
                f"{free_space_bytes} bytes available, {required_bytes} bytes required"
            )

        session_results = self.sessions.ensure_all_ready(job_id)
        return PreflightResult(
            worker_id=normalized_worker_id,
            storage_writable=True,
            free_space_bytes=free_space_bytes,
            sessions=dict(session_results),
        )
