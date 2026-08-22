import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.cloud_agent import ServiceSessionStatus, SessionCheckResult
from app.services.cloud_agent.errors import HumanRequiredError


GIB = 1024**3


class FakeStore:
    def __init__(self, *, worker_id="worker-a", heartbeat="2026-08-22T14:00:00+00:00", events=None):
        self.job = SimpleNamespace(id="job-1", worker_id=worker_id)
        self.heartbeat = heartbeat
        self.events = events if events is not None else []

    def get_job(self, job_id):
        self.events.append("job")
        assert job_id == "job-1"
        return self.job

    def get_worker_last_seen(self, worker_id=None):
        self.events.append("heartbeat")
        return self.heartbeat


class FakeStorage:
    def __init__(self, root: Path):
        self.root = root


class FakeSessions:
    def __init__(self, *, error=None, events=None):
        self.error = error
        self.events = events if events is not None else []
        self.calls = []

    def ensure_all_ready(self, job_id):
        self.events.append("sessions")
        self.calls.append(job_id)
        if self.error is not None:
            raise self.error
        return {
            "google_flow": SessionCheckResult(
                service="google_flow",
                status=ServiceSessionStatus.READY,
                checked_at="2026-08-22T14:00:00+00:00",
            ),
            "canva": SessionCheckResult(
                service="canva",
                status=ServiceSessionStatus.READY,
                checked_at="2026-08-22T14:00:00+00:00",
            ),
        }


def _manager_cls():
    module = importlib.import_module("app.services.cloud_agent.preflight")
    return module.PreflightManager


def _build_manager(
    tmp_path,
    *,
    store=None,
    sessions=None,
    writable=True,
    free_bytes=20 * GIB,
    events=None,
):
    events = events if events is not None else []
    store = store or FakeStore(events=events)
    sessions = sessions or FakeSessions(events=events)

    def writable_probe(root):
        events.append("writable")
        assert Path(root) == tmp_path / "jobs"
        return writable

    def disk_usage(root):
        events.append("disk")
        assert Path(root) == tmp_path / "jobs"
        return SimpleNamespace(total=100 * GIB, used=100 * GIB - free_bytes, free=free_bytes)

    manager = _manager_cls()(
        store,
        FakeStorage(tmp_path / "jobs"),
        sessions,
        min_free_disk_gb=10,
        storage_writable_probe=writable_probe,
        disk_usage=disk_usage,
    )
    return manager, sessions, events


def test_preflight_success_checks_worker_storage_disk_then_sessions(tmp_path):
    manager, sessions, events = _build_manager(tmp_path)

    result = manager.ensure_ready("job-1", worker_id="worker-a")

    assert events == ["job", "heartbeat", "writable", "disk", "sessions"]
    assert result.worker_id == "worker-a"
    assert result.storage_writable is True
    assert result.free_space_bytes == 20 * GIB
    assert set(result.sessions) == {"google_flow", "canva"}
    assert sessions.calls == ["job-1"]


def test_preflight_rejects_wrong_worker_before_storage_or_sessions(tmp_path):
    events = []
    store = FakeStore(worker_id="worker-other", events=events)
    manager, sessions, _ = _build_manager(tmp_path, store=store, events=events)

    with pytest.raises(RuntimeError, match="worker"):
        manager.ensure_ready("job-1", worker_id="worker-a")

    assert events == ["job"]
    assert sessions.calls == []


def test_preflight_rejects_missing_worker_heartbeat_before_storage(tmp_path):
    events = []
    store = FakeStore(heartbeat=None, events=events)
    manager, sessions, _ = _build_manager(tmp_path, store=store, events=events)

    with pytest.raises(RuntimeError, match="heartbeat"):
        manager.ensure_ready("job-1", worker_id="worker-a")

    assert events == ["job", "heartbeat"]
    assert sessions.calls == []


def test_preflight_rejects_unwritable_storage_before_disk_or_sessions(tmp_path):
    manager, sessions, events = _build_manager(tmp_path, writable=False)

    with pytest.raises(RuntimeError, match="writable"):
        manager.ensure_ready("job-1", worker_id="worker-a")

    assert events == ["job", "heartbeat", "writable"]
    assert sessions.calls == []


def test_preflight_rejects_disk_below_configured_minimum_before_sessions(tmp_path):
    manager, sessions, events = _build_manager(tmp_path, free_bytes=9 * GIB)

    with pytest.raises(RuntimeError, match="free disk"):
        manager.ensure_ready("job-1", worker_id="worker-a")

    assert events == ["job", "heartbeat", "writable", "disk"]
    assert sessions.calls == []


def test_preflight_preserves_human_required_from_session_layer(tmp_path):
    events = []
    sessions = FakeSessions(
        error=HumanRequiredError("canva: 2FA_REQUIRED"),
        events=events,
    )
    manager, _, _ = _build_manager(tmp_path, sessions=sessions, events=events)

    with pytest.raises(HumanRequiredError, match="2FA_REQUIRED"):
        manager.ensure_ready("job-1", worker_id="worker-a")

    assert events == ["job", "heartbeat", "writable", "disk", "sessions"]
