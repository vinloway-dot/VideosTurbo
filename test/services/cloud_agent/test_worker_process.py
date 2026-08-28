import os
import time

from app.services.cloud_agent.progress import ProgressSignal
from app.services.cloud_agent.worker_process import (
    ChildWaitResult,
    MultiprocessingJobProcessLauncher,
)


def _signal_only_child(_db_path, job_id, _worker_id, endpoint):
    endpoint.publish_nowait(
        ProgressSignal(
            job_id=job_id,
            milestone="child.started",
            occurred_at="2026-08-28T00:00:00+00:00",
        )
    )


def _blocking_child(_db_path, _job_id, _worker_id, _endpoint):
    while True:
        time.sleep(0.01)


def test_child_wait_returns_progress_then_clean_exit(tmp_path):
    launcher = MultiprocessingJobProcessLauncher(
        db_path=str(tmp_path / "agent.sqlite3"),
        child_target=_signal_only_child,
    )
    child = launcher.start("job-1", "worker-1")

    first = child.wait(2.0)
    assert first.progress_signal is not None
    assert first.progress_signal.milestone == "child.started"

    deadline = time.monotonic() + 2.0
    result = ChildWaitResult(False, None, None)
    while not result.exited and time.monotonic() < deadline:
        result = child.wait(0.05)
    assert result.exited is True
    assert result.exit_code == 0


def test_terminate_group_confirms_child_is_stopped(tmp_path):
    launcher = MultiprocessingJobProcessLauncher(
        db_path=str(tmp_path / "agent.sqlite3"),
        child_target=_blocking_child,
    )
    child = launcher.start("job-1", "worker-1")

    assert child.is_alive()
    assert child.terminate_group(grace_seconds=0.2) is True
    assert child.is_alive() is False


def test_child_process_has_a_distinct_process_group(tmp_path):
    launcher = MultiprocessingJobProcessLauncher(
        db_path=str(tmp_path / "agent.sqlite3"),
        child_target=_blocking_child,
    )
    child = launcher.start("job-1", "worker-1")
    deadline = time.monotonic() + 2.0
    while os.getpgid(child.pid) != child.pid and time.monotonic() < deadline:
        time.sleep(0.01)

    assert os.getpgid(child.pid) == child.pid
    assert child.terminate_group(grace_seconds=0.2) is True
