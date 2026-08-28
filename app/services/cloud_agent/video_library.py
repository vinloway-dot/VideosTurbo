"""Library visibility and deletion for completed Cloud Agent videos."""

from dataclasses import dataclass
from math import ceil

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobRecord, CloudJobStatus
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.storage import CloudJobStorage


class VideoLibraryNotFoundError(LookupError):
    """Raised when a job is not visible in the completed-video library."""


@dataclass(frozen=True)
class VideoLibraryItem:
    job_id: str
    subject: str
    completed_at: str

    @classmethod
    def from_job(cls, job: CloudJobRecord) -> "VideoLibraryItem":
        return cls(job_id=job.id, subject=job.subject, completed_at=job.completed_at)


@dataclass(frozen=True)
class VideoLibraryPage:
    items: tuple[VideoLibraryItem, ...]
    page: int
    page_size: int
    total_items: int
    total_pages: int

    @classmethod
    def from_items(
        cls, items: tuple[VideoLibraryItem, ...], *, page: int, page_size: int
    ) -> "VideoLibraryPage":
        total_items = len(items)
        total_pages = max(1, ceil(total_items / page_size))
        start = (page - 1) * page_size
        return cls(
            items=items[start : start + page_size],
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        )


class CloudVideoLibraryService:
    def __init__(self, *, store: CloudJobStore, storage: CloudJobStorage):
        self._store = store
        self._storage = storage

    def list_videos(self, *, page: int, page_size: int) -> VideoLibraryPage:
        if page < 1 or page_size != 10:
            raise ValueError("invalid video library page")
        visible = tuple(
            VideoLibraryItem.from_job(job)
            for job in self._store.list_completed_final_candidates()
            if self._is_visible(job)
        )
        return VideoLibraryPage.from_items(visible, page=page, page_size=page_size)

    def delete_video(self, job_id: str) -> None:
        job = self._store.get_job(job_id)
        if job is None or not self._is_visible(job):
            raise VideoLibraryNotFoundError(job_id)
        staged = self._storage.stage_job_artifacts(job.id)
        try:
            self._store.delete_job(job.id)
        except Exception:
            self._storage.restore_staged_job(job.id, staged)
            raise
        self._storage.purge_staged_job(staged)

    def _is_visible(self, job: CloudJobRecord) -> bool:
        return (
            job.status is CloudJobStatus.COMPLETED
            and job.checkpoint
            in {CloudJobCheckpoint.FINAL_VALIDATED, CloudJobCheckpoint.COMPLETED}
            and self._storage.has_valid_final_video(job.id, job.final_video)
        )
