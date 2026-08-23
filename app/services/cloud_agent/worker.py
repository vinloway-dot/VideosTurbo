import os
import socket
import threading
import time
import uuid
from typing import Protocol

from app.models.cloud_agent import CloudJobRecord
from app.services.cloud_agent.job_store import CloudJobStore


class WorkflowRunner(Protocol):
    def run(self, job_id: str, *, worker_id: str) -> CloudJobRecord: ...


class CloudAgentWorker:
    def __init__(
        self,
        store: CloudJobStore,
        workflow: WorkflowRunner,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 120,
        lease_renew_interval_seconds: float | None = None,
        poll_seconds: float = 2.0,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")

        renew_interval = (
            float(lease_renew_interval_seconds)
            if lease_renew_interval_seconds is not None
            else lease_seconds / 3.0
        )
        if renew_interval <= 0 or renew_interval >= lease_seconds:
            raise ValueError("lease renewal interval must be positive and shorter than lease")

        self.store = store
        self.workflow = workflow
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
        )
        self.lease_seconds = lease_seconds
        self.lease_renew_interval_seconds = renew_interval
        self.poll_seconds = poll_seconds

    def _renew_lease_until_stopped(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.lease_renew_interval_seconds):
            if not self.store.renew_lease(job_id, self.worker_id, self.lease_seconds):
                return

    def run_once(self) -> bool:
        self.store.update_worker_heartbeat(self.worker_id)
        job = self.store.claim_next_job(self.worker_id, self.lease_seconds)
        if job is None:
            return False

        stop_renewal = threading.Event()
        renewal_thread = threading.Thread(
            target=self._renew_lease_until_stopped,
            args=(job.id, stop_renewal),
            daemon=True,
            name=f"cloud-agent-lease-{job.id}",
        )
        renewal_thread.start()
        try:
            self.workflow.run(job.id, worker_id=self.worker_id)
        finally:
            stop_renewal.set()
            renewal_thread.join()
            self.store.release_lease(job.id, self.worker_id)
        return True

    def run_forever(self) -> None:
        while True:
            if not self.run_once():
                time.sleep(self.poll_seconds)


def main() -> None:
    """Run the production worker when invoked as a module by systemd."""
    from app.services.cloud_agent.factory import build_worker

    build_worker().run_forever()


if __name__ == "__main__":
    main()
