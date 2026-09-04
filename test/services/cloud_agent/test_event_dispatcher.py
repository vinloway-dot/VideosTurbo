import threading
import time
from types import SimpleNamespace

import pytest
import requests

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobStatus
from app.services.cloud_agent.event_dispatcher import (
    CloudJobEventDispatcher,
    RequestsJobEventTransport,
)
from app.services.cloud_agent.job_events import CloudJobEvent, CloudJobEventType


def _event(job_id="job-1"):
    return CloudJobEvent(
        event_id=f"event-{job_id}", type=CloudJobEventType.JOB_UPDATED,
        job_id=job_id, status=CloudJobStatus.TTS_GENERATING,
        checkpoint=CloudJobCheckpoint.NONE, current_step="tts_generating",
        progress=15, updated_at="2026-08-28T00:00:00+00:00", completed_at="",
    )


def _wait_until(predicate):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_publish_nowait_returns_immediately_and_dispatches_in_background():
    delivered = threading.Event()
    dispatcher = CloudJobEventDispatcher(transport=lambda event: delivered.set(), queue_size=2)
    try:
        assert dispatcher.publish_nowait(_event()) is True
        assert delivered.wait(timeout=1.0)
    finally:
        dispatcher.close()


def test_full_queue_drops_signal_without_raising():
    release = threading.Event()
    started = threading.Event()

    def blocked(_event):
        started.set()
        release.wait(timeout=1.0)

    dispatcher = CloudJobEventDispatcher(transport=blocked, queue_size=1)
    try:
        assert dispatcher.publish_nowait(_event("one")) is True
        assert started.wait(timeout=1.0)
        assert dispatcher.publish_nowait(_event("two")) is True
        assert dispatcher.publish_nowait(_event("three")) is False
    finally:
        release.set()
        dispatcher.close()


def test_transport_exception_does_not_kill_dispatcher_or_raise_to_publisher():
    calls = []

    def failing(event):
        calls.append(event.job_id)
        raise requests.Timeout("offline")

    dispatcher = CloudJobEventDispatcher(transport=failing, queue_size=2)
    try:
        assert dispatcher.publish_nowait(_event("job-1")) is True
        assert _wait_until(lambda: calls == ["job-1"])
    finally:
        dispatcher.close()


def test_requests_transport_posts_only_safe_json_with_timeout(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(requests, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    event = _event("job-1")
    RequestsJobEventTransport(
        "http://127.0.0.1:8080/api/v1/cloud-agent/internal/events", timeout_seconds=0.5
    ).send(event)
    assert calls == [(
        "http://127.0.0.1:8080/api/v1/cloud-agent/internal/events",
        {"json": event.model_dump(mode="json"), "timeout": 0.5},
    )]
    assert "script" not in calls[0][1]["json"]
    assert "final_video" not in calls[0][1]["json"]


def test_transport_rejects_non_loopback_or_wrong_path():
    with pytest.raises(ValueError):
        RequestsJobEventTransport("https://example.com/api/v1/cloud-agent/internal/events", timeout_seconds=0.5)
    with pytest.raises(ValueError):
        RequestsJobEventTransport("http://127.0.0.1:8080/other", timeout_seconds=0.5)


def test_worker_factory_uses_event_store_but_controller_store_does_not(monkeypatch, tmp_path):
    from app.config import config
    from app.controllers.v1 import cloud_agent as controller
    from app.services.cloud_agent import factory
    from app.services.cloud_agent.job_events import EventPublishingCloudJobStore
    from app.services.cloud_agent.job_store import CloudJobStore

    monkeypatch.setitem(config.app, "cloud_agent_db_path", str(tmp_path / "agent.sqlite3"))
    class Sink:
        def publish_nowait(self, _event):
            return True

    monkeypatch.setattr(factory, "CloudJobEventDispatcher", lambda **_kw: Sink())
    monkeypatch.setattr(factory, "RequestsJobEventTransport", lambda *args, **kwargs: SimpleNamespace(send=lambda event: None))
    monkeypatch.setattr(factory, "CloudAgentWorker", lambda store, **kw: store)

    worker_store = factory.build_worker()
    controller_store = controller.get_cloud_job_store()
    assert isinstance(worker_store, EventPublishingCloudJobStore)
    assert type(controller_store) is CloudJobStore
