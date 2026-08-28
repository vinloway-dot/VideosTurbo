import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from loguru import logger

from app.models.cloud_agent import CloudJobRecord
from app.services.cloud_agent.job_store import CloudJobStore


_SAFE_MILESTONE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True)
class ProgressSignal:
    job_id: str
    milestone: str
    occurred_at: str


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ProgressReporter(Protocol):
    def reached(self, job_id: str, milestone: str) -> CloudJobRecord: ...


class ProgressSignalSink(Protocol):
    def publish_nowait(self, signal: ProgressSignal) -> bool: ...


class NullProgressSignalSink:
    def publish_nowait(self, signal: ProgressSignal) -> bool:
        del signal
        return True


def normalize_milestone(milestone: str) -> str:
    normalized = str(milestone or "").strip()
    if not _SAFE_MILESTONE.fullmatch(normalized):
        raise ValueError("milestone must be 1-128 safe ASCII characters")
    return normalized


class DurableProgressReporter:
    def __init__(
        self,
        store: CloudJobStore,
        *,
        sink: ProgressSignalSink | None = None,
        clock: Clock | None = None,
    ):
        self._store = store
        self._sink = sink or NullProgressSignalSink()
        self._clock = clock or SystemClock()

    def reached(self, job_id: str, milestone: str) -> CloudJobRecord:
        normalized = normalize_milestone(milestone)
        before = self._store.get_job(job_id)
        if before is None:
            raise KeyError(job_id)
        after = self._store.mark_progress(
            job_id,
            normalized,
            at=self._clock.now(),
        )
        if after.last_progress_at == before.last_progress_at:
            return after
        signal = ProgressSignal(
            job_id=job_id,
            milestone=normalized,
            occurred_at=after.last_progress_at,
        )
        try:
            self._sink.publish_nowait(signal)
        except Exception as exc:
            logger.warning(
                "cloud progress signal dropped job_id={} error_type={}",
                job_id,
                type(exc).__name__,
            )
        return after
