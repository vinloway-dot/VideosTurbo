from enum import Enum
from typing import Literal, Protocol
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


class CloudJobIncidentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    type: Literal["job.incident"] = "job.incident"
    incident_id: str = Field(min_length=1, max_length=64)
    former_job_id: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=128)
    stage: str = Field(min_length=1, max_length=64)
    created_at: str = Field(min_length=1, max_length=64)


CloudAgentEvent = CloudJobEvent | CloudJobIncidentEvent


class JobEventSink(Protocol):
    def publish_nowait(self, event: CloudAgentEvent) -> bool: ...


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
        self.publish_snapshot(after, event_type=event_type)
        return after

    def reserve_flow_workspace_retry(
        self,
        job_id: str,
        *,
        delay_seconds: float,
        worker_id: str,
    ) -> CloudJobRecord | None:
        after = super().reserve_flow_workspace_retry(
            job_id,
            delay_seconds=delay_seconds,
            worker_id=worker_id,
        )
        if after is not None:
            self.publish_snapshot(after, event_type=CloudJobEventType.JOB_UPDATED)
        return after

    def begin_flow_workspace_retry_opening(
        self,
        job_id: str,
        *,
        worker_id: str,
    ) -> CloudJobRecord | None:
        after = super().begin_flow_workspace_retry_opening(
            job_id,
            worker_id=worker_id,
        )
        if after is not None:
            self.publish_snapshot(after, event_type=CloudJobEventType.JOB_UPDATED)
        return after

    def publish_snapshot(
        self,
        job: CloudJobRecord,
        *,
        event_type: CloudJobEventType | None = None,
    ) -> bool:
        resolved_type = event_type or (
            CloudJobEventType.JOB_COMPLETED
            if job.status is CloudJobStatus.COMPLETED
            else CloudJobEventType.JOB_UPDATED
        )
        event = CloudJobEvent(
            event_id=uuid4().hex,
            type=resolved_type,
            job_id=job.id,
            status=job.status,
            checkpoint=job.checkpoint,
            current_step=job.current_step,
            progress=job.progress,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
        )
        try:
            return self._event_sink.publish_nowait(event)
        except Exception as exc:  # event delivery must never break workflow writes
            logger.warning(
                "cloud job event publish failed type={} job_id={} error_type={}",
                event.type.value,
                event.job_id,
                type(exc).__name__,
            )
            return False
