import multiprocessing
import os
import queue
import signal
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.models.cloud_agent import CloudJobIncident
from app.services.cloud_agent.progress import ProgressSignal


@dataclass(frozen=True)
class ChildWaitResult:
    exited: bool
    exit_code: int | None
    progress_signal: ProgressSignal | None


class JobChildHandle(Protocol):
    def wait(self, timeout_seconds: float) -> ChildWaitResult: ...

    def is_alive(self) -> bool: ...

    def terminate_group(self, grace_seconds: float) -> bool: ...


class JobProcessLauncher(Protocol):
    def start(self, job_id: str, worker_id: str) -> JobChildHandle: ...


class JobTerminationPort(Protocol):
    def delete_stopped_job(
        self,
        job_id: str,
        *,
        child_stopped: bool,
        reason_code: str,
        stage: str,
    ) -> CloudJobIncident: ...


class MultiprocessingProgressEndpoint:
    def __init__(self, progress_queue):
        self._queue = progress_queue

    def publish_nowait(self, signal_value: ProgressSignal) -> bool:
        try:
            self._queue.put_nowait(signal_value)
        except queue.Full:
            return False
        return True


def run_job_child(
    db_path: str,
    job_id: str,
    worker_id: str,
    signal_endpoint: MultiprocessingProgressEndpoint,
) -> None:
    """Build and run all browser-bound objects inside the isolated child."""
    from app.services.cloud_agent.factory import build_job_child

    runtime = build_job_child(db_path=db_path, progress_sink=signal_endpoint)
    try:
        runtime.run(job_id, worker_id=worker_id)
    finally:
        runtime.close()


def _child_process_entry(
    child_target: Callable,
    db_path: str,
    job_id: str,
    worker_id: str,
    progress_queue,
) -> None:
    if hasattr(os, "setsid"):
        os.setsid()
    endpoint = MultiprocessingProgressEndpoint(progress_queue)
    child_target(db_path, job_id, worker_id, endpoint)


class MultiprocessingJobChildHandle:
    def __init__(self, process, progress_queue):
        self._process = process
        self._progress_queue = progress_queue

    @property
    def pid(self) -> int:
        if self._process.pid is None:
            raise RuntimeError("child process has not started")
        return self._process.pid

    def wait(self, timeout_seconds: float) -> ChildWaitResult:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        try:
            progress = self._progress_queue.get(timeout=timeout_seconds)
            return ChildWaitResult(
                exited=not self._process.is_alive(),
                exit_code=self._process.exitcode,
                progress_signal=progress,
            )
        except queue.Empty:
            self._process.join(timeout=0)
            return ChildWaitResult(
                exited=not self._process.is_alive(),
                exit_code=self._process.exitcode,
                progress_signal=None,
            )

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def terminate_group(self, grace_seconds: float) -> bool:
        if grace_seconds < 0:
            raise ValueError("grace_seconds must not be negative")
        if not self._process.is_alive():
            self._process.join(timeout=0)
            return True

        pid = self.pid
        try:
            if hasattr(os, "killpg") and os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGTERM)
            else:
                self._process.terminate()
        except ProcessLookupError:
            pass
        self._process.join(timeout=grace_seconds)
        if self._process.is_alive():
            try:
                if hasattr(os, "killpg") and os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    self._process.kill()
            except ProcessLookupError:
                pass
            self._process.join(timeout=max(grace_seconds, 0.1))
        return not self._process.is_alive()


class MultiprocessingJobProcessLauncher:
    def __init__(
        self,
        *,
        db_path: str,
        signal_queue_size: int = 64,
        child_target: Callable = run_job_child,
    ):
        if signal_queue_size <= 0:
            raise ValueError("signal_queue_size must be positive")
        self._db_path = str(db_path)
        self._signal_queue_size = signal_queue_size
        self._child_target = child_target
        self._context = multiprocessing.get_context("spawn")

    def start(self, job_id: str, worker_id: str) -> MultiprocessingJobChildHandle:
        progress_queue = self._context.Queue(maxsize=self._signal_queue_size)
        process = self._context.Process(
            target=_child_process_entry,
            args=(
                self._child_target,
                self._db_path,
                job_id,
                worker_id,
                progress_queue,
            ),
            daemon=False,
            name=f"cloud-agent-job-{job_id}",
        )
        process.start()
        return MultiprocessingJobChildHandle(process, progress_queue)
