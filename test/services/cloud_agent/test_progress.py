from datetime import datetime, timedelta, timezone

import pytest

from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.progress import DurableProgressReporter

from .test_job_store import _request


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 28, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, *, seconds: int):
        self.value += timedelta(seconds=seconds)


class RecordingProgressSink:
    def __init__(self):
        self.items = []

    def publish_nowait(self, signal):
        self.items.append(signal)
        return True


def test_new_milestone_advances_timestamp_and_signals_once(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    sink = RecordingProgressSink()
    clock = MutableClock()
    reporter = DurableProgressReporter(store, sink=sink, clock=clock)

    first = reporter.reached(job.id, "flow.inventory.5")
    clock.advance(seconds=30)
    same = reporter.reached(job.id, "flow.inventory.5")

    assert same.last_progress_at == first.last_progress_at
    assert [item.milestone for item in sink.items] == ["flow.inventory.5"]


def test_progress_sink_failure_does_not_rollback_timestamp(tmp_path):
    class FailingSink:
        def publish_nowait(self, _signal):
            raise RuntimeError("queue unavailable")

    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    reporter = DurableProgressReporter(store, sink=FailingSink(), clock=MutableClock())

    updated = reporter.reached(job.id, "canva.audio.inserted")

    assert store.get_job(job.id).last_progress_at == updated.last_progress_at


@pytest.mark.parametrize(
    "milestone",
    ["", "has spaces", "caption/unsafe", "x" * 129],
)
def test_progress_rejects_unsafe_milestone_identifiers(tmp_path, milestone):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    reporter = DurableProgressReporter(
        store,
        sink=RecordingProgressSink(),
        clock=MutableClock(),
    )

    with pytest.raises(ValueError):
        reporter.reached(job.id, milestone)
