import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobRecord, CloudJobStatus
from app.services.cloud_agent.errors import RecoveryBudgetExhausted
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.progress import Clock, SystemClock
from app.services.cloud_agent.worker_process import (
    JobChildHandle,
    JobProcessLauncher,
    JobTerminationPort,
)


class WorkflowRunner(Protocol):
    def run(self, job_id: str, *, worker_id: str) -> CloudJobRecord: ...


class CloudAgentWorker:
    def __init__(
        self,
        store: CloudJobStore,
        workflow: WorkflowRunner | None = None,
        *,
        process_launcher: JobProcessLauncher | None = None,
        termination_service: JobTerminationPort | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 120,
        lease_renew_interval_seconds: float | None = None,
        poll_seconds: float = 2.0,
        canva_stall_seconds: float = 1200.0,
        job_stall_seconds: float = 3600.0,
        child_terminate_grace_seconds: float = 15.0,
        clock: Clock | None = None,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if workflow is None and process_launcher is None:
            raise ValueError("workflow or process_launcher is required")
        if workflow is not None and process_launcher is not None:
            raise ValueError("workflow and process_launcher are mutually exclusive")
        if canva_stall_seconds <= 0:
            raise ValueError("canva_stall_seconds must be positive")
        if job_stall_seconds <= canva_stall_seconds:
            raise ValueError("job_stall_seconds must exceed canva_stall_seconds")
        if child_terminate_grace_seconds <= 0:
            raise ValueError("child_terminate_grace_seconds must be positive")

        renew_interval = (
            float(lease_renew_interval_seconds)
            if lease_renew_interval_seconds is not None
            else lease_seconds / 3.0
        )
        if renew_interval <= 0 or renew_interval >= lease_seconds:
            raise ValueError("lease renewal interval must be positive and shorter than lease")

        self.store = store
        self.workflow = workflow
        self.process_launcher = process_launcher
        self.termination_service = termination_service
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
        )
        self.lease_seconds = lease_seconds
        self.lease_renew_interval_seconds = renew_interval
        self.poll_seconds = poll_seconds
        self.canva_stall_seconds = float(canva_stall_seconds)
        self.job_stall_seconds = float(job_stall_seconds)
        self.child_terminate_grace_seconds = float(child_terminate_grace_seconds)
        self.clock = clock or SystemClock()

    def _renew_lease_until_stopped(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.lease_renew_interval_seconds):
            if not self.store.renew_lease(job_id, self.worker_id, self.lease_seconds):
                return

    def _run_inline(self, job: CloudJobRecord) -> None:
        if self.workflow is None:
            raise RuntimeError("inline workflow is not configured")
        stop_renewal = threading.Event()
        renewal_thread = threading.Thread(
            target=self._renew_lease_until_stopped,
            args=(job.id, stop_renewal),
            daemon=True,
            name=f"cloud-agent-lease-{job.id}",
        )
        renewal_thread.start()
        try:
            try:
                self.workflow.run(job.id, worker_id=self.worker_id)
            except Exception as exc:
                self._record_runtime_error(job.id, exc)
        finally:
            stop_renewal.set()
            renewal_thread.join()

    @staticmethod
    def _timestamp(value: str) -> datetime | None:
        if not str(value or "").strip():
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _is_canva_active(job: CloudJobRecord) -> bool:
        return (
            job.checkpoint is CloudJobCheckpoint.FLOW_READY
            and job.status
            in {
                CloudJobStatus.FLOW_READY,
                CloudJobStatus.CANVA_UPLOADING,
                CloudJobStatus.CANVA_EDITING,
                CloudJobStatus.CAPTIONING,
                CloudJobStatus.EXPORTING,
                CloudJobStatus.DOWNLOADING_FINAL,
                CloudJobStatus.VALIDATING,
            }
        )

    def _record_runtime_error(self, job_id: str, exc: Exception) -> None:
        if self.store.get_job(job_id) is None:
            return
        self.store.patch_job(
            job_id,
            status=CloudJobStatus.HUMAN_REQUIRED,
            current_step="human_required",
            error_code="WORKER_RUNTIME_ERROR",
            error_message=f"Cloud Agent workflow stopped: {type(exc).__name__}",
        )

    def _stop_child(self, job_id: str, child: JobChildHandle) -> bool:
        stopped = child.terminate_group(self.child_terminate_grace_seconds)
        if stopped:
            return True
        self.store.patch_job(
            job_id,
            status=CloudJobStatus.HUMAN_REQUIRED,
            current_step="human_required",
            error_code="CHILD_TERMINATION_FAILED",
            error_message="Cloud Agent child process could not be stopped safely.",
        )
        return False

    def _delete_terminal_job(self, job: CloudJobRecord, *, reason: str) -> None:
        stage = "canva" if self._is_canva_active(job) else "google_flow"
        if self.termination_service is None:
            self.store.patch_job(
                job.id,
                status=CloudJobStatus.HUMAN_REQUIRED,
                current_step="delete_pending",
                error_code=reason,
                error_message="Cloud Agent job requires terminal cleanup.",
            )
            return
        self.termination_service.delete_stopped_job(
            job.id,
            child_stopped=True,
            reason_code=reason,
            stage=stage,
        )

    def _handle_child_exit(self, job_id: str, exit_code: int | None) -> None:
        persisted = self.store.get_job(job_id)
        if persisted is None:
            return
        if persisted.error_code in {
            "FLOW_RECOVERY_EXHAUSTED",
            "CANVA_RESTART_EXHAUSTED",
        }:
            self._delete_terminal_job(persisted, reason=persisted.error_code)
            return
        if exit_code not in (0, None) and persisted.status not in {
            CloudJobStatus.COMPLETED,
            CloudJobStatus.HUMAN_REQUIRED,
            CloudJobStatus.FAILED,
            CloudJobStatus.CANCELLED,
            CloudJobStatus.PAUSED,
        }:
            self._record_runtime_error(job_id, RuntimeError("child process exited"))

    def _run_supervised(self, job: CloudJobRecord) -> None:
        if self.process_launcher is None:
            raise RuntimeError("process launcher is not configured")
        if not job.last_progress_at:
            job = self.store.mark_progress(
                job.id,
                "worker.claimed",
                at=self.clock.now(),
            )
        child = self.process_launcher.start(job.id, self.worker_id)
        next_lease_renewal = self.clock.now() + timedelta(
            seconds=self.lease_renew_interval_seconds
        )

        while True:
            persisted = self.store.get_job(job.id)
            if persisted is None:
                if child.is_alive():
                    self._stop_child(job.id, child)
                return
            now = self.clock.now()
            last_progress = self._timestamp(persisted.last_progress_at) or now
            global_deadline = last_progress + timedelta(seconds=self.job_stall_seconds)
            deadlines = [next_lease_renewal, global_deadline]
            canva_deadline = None
            if self._is_canva_active(persisted):
                canva_started = self._timestamp(persisted.canva_attempt_started_at)
                canva_reference = max(
                    item for item in (last_progress, canva_started) if item is not None
                )
                canva_deadline = canva_reference + timedelta(
                    seconds=self.canva_stall_seconds
                )
                deadlines.append(canva_deadline)
            wait_seconds = max(0.0, (min(deadlines) - now).total_seconds())
            result = child.wait(wait_seconds)
            if result.exited:
                self._handle_child_exit(job.id, result.exit_code)
                return

            persisted = self.store.get_job(job.id)
            if persisted is None:
                if child.is_alive():
                    self._stop_child(job.id, child)
                return
            now = self.clock.now()
            last_progress = self._timestamp(persisted.last_progress_at) or now
            if now >= last_progress + timedelta(seconds=self.job_stall_seconds):
                if self._stop_child(job.id, child):
                    self._delete_terminal_job(
                        persisted,
                        reason="JOB_STALLED_TIMEOUT",
                    )
                return

            if self._is_canva_active(persisted):
                canva_started = self._timestamp(persisted.canva_attempt_started_at)
                canva_reference = max(
                    item for item in (last_progress, canva_started) if item is not None
                )
                if now >= canva_reference + timedelta(
                    seconds=self.canva_stall_seconds
                ):
                    if not self._stop_child(job.id, child):
                        return
                    try:
                        self.store.reserve_canva_restart(job.id)
                    except RecoveryBudgetExhausted:
                        exhausted = self.store.patch_job(
                            job.id,
                            status=CloudJobStatus.HUMAN_REQUIRED,
                            current_step="delete_pending",
                            error_code="CANVA_RESTART_EXHAUSTED",
                            error_message="Canva restart budget exhausted.",
                        )
                        self._delete_terminal_job(
                            exhausted,
                            reason="CANVA_RESTART_EXHAUSTED",
                        )
                        return
                    child = self.process_launcher.start(job.id, self.worker_id)
                    next_lease_renewal = now + timedelta(
                        seconds=self.lease_renew_interval_seconds
                    )
                    continue

            if now >= next_lease_renewal:
                if not self.store.renew_lease(
                    job.id,
                    self.worker_id,
                    self.lease_seconds,
                ):
                    if child.is_alive():
                        self._stop_child(job.id, child)
                    return
                next_lease_renewal = now + timedelta(
                    seconds=self.lease_renew_interval_seconds
                )

    def run_once(self) -> bool:
        self.store.update_worker_heartbeat(self.worker_id)
        job = self.store.claim_next_job(self.worker_id, self.lease_seconds)
        if job is None:
            return False
        try:
            if self.process_launcher is not None:
                self._run_supervised(job)
            else:
                self._run_inline(job)
        finally:
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
