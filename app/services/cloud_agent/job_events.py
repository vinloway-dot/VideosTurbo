from enum import Enum
from typing import Protocol
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobRecord, CloudJobStatus
from app.services.cloud_agent.job_store import CloudJobStore


class CloudJobEventType(str, Enum):
    JOB_UPDATED = "job.updated"
    JOB_COMPLETED = "job.completed"


class CloudJobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    type: CloudJobEventType
    job_id: str = Field(min_length=1, max_length=64)
    status: CloudJobStatus
    checkpoint: CloudJobCheckpoint
    current_step: str = Field(max_length=128)
    progress: int = Field(ge=0, le=100)
    updated_at: str = Field(min_length=1, max_length=64)
    completed_at: str = Field(max_length=64)


class JobEventSink(Protocol):
    def publish_nowait(self, event: CloudJobEvent) -> bool: ...


class EventPublishingCloudJobStore(CloudJobStore):
    """CloudJobStore decorator that emits safe events after durable writes."""

    def __init__(self, db_path: str, *, sink: JobEventSink):
        super().__init__(db_path)
        self._event_sink = sink

    def patch_job(self, job_id: str, **changes) -> CloudJobRecord:
        if not changes:
            return super().patch_job(job_id, **changes)

        before = self.get_job(job_id)
        if before is None:
            raise KeyError(job_id)
        after = super().patch_job(job_id, **changes)

        projection_before = (
            before.status,
            before.checkpoint,
            before.current_step,
            before.progress,
        )
        projection_after = (
            after.status,
            after.checkpoint,
            after.current_step,
            after.progress,
        )
        if projection_before == projection_after:
            return after

        event_type = (
            CloudJobEventType.JOB_COMPLETED
            if before.status is not CloudJobStatus.COMPLETED
            and after.status is CloudJobStatus.COMPLETED
            else CloudJobEventType.JOB_UPDATED
        )
        event = CloudJobEvent(
            event_id=uuid4().hex,
            type=event_type,
            job_id=after.id,
            status=after.status,
            checkpoint=after.checkpoint,
            current_step=after.current_step,
            progress=after.progress,
            updated_at=after.updated_at,
            completed_at=after.completed_at,
        )
        try:
            self._event_sink.publish_nowait(event)
        except Exception as exc:  # event delivery must never break workflow writes
            logger.warning(
                "cloud job event publish failed type={} job_id={} error_type={}",
                event.type.value,
                event.job_id,
                type(exc).__name__,
            )
        return after
