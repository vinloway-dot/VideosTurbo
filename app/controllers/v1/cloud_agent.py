import shutil
from pathlib import Path

from fastapi import Depends, Request
from fastapi.responses import FileResponse

from app.config import config
from app.controllers.v1.base import new_router
from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobDraftRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
)
from app.services.llm import generate_script
from app.services.six_clip_plan import build_master_prompt, generate_six_clip_plan
from app.models.exception import HttpException
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.errors import HumanRequiredError
from app.services.cloud_agent.errors import PreFlowRetryEligibilityError
from app.services.cloud_agent.factory import (
    build_pre_flow_retry_service,
    build_session_manager,
)
from app.services.cloud_agent.retry import PreFlowRetryService
from app.services.cloud_agent.preflight import _probe_storage_writable
from app.services.cloud_agent.session import SessionManager
from app.services.cloud_agent.storage import CloudJobStorage
from app.utils import utils
from app.utils.file_security import resolve_path_within_directory


router = new_router()


_ACTIVE_JOB_STATUSES = {
    CloudJobStatus.PREFLIGHT,
    CloudJobStatus.PREFLIGHT_PASSED,
    CloudJobStatus.TTS_GENERATING,
    CloudJobStatus.TTS_READY,
    CloudJobStatus.FLOW_GENERATING,
    CloudJobStatus.FLOW_DOWNLOADING,
    CloudJobStatus.FLOW_READY,
    CloudJobStatus.CANVA_UPLOADING,
    CloudJobStatus.CANVA_EDITING,
    CloudJobStatus.CAPTIONING,
    CloudJobStatus.EXPORTING,
    CloudJobStatus.DOWNLOADING_FINAL,
    CloudJobStatus.VALIDATING,
    CloudJobStatus.FINAL_VALIDATED,
}
_TERMINAL_JOB_STATUSES = {
    CloudJobStatus.COMPLETED,
    CloudJobStatus.FAILED,
    CloudJobStatus.CANCELLED,
}
_FINAL_CHECKPOINTS = {
    CloudJobCheckpoint.FINAL_VALIDATED,
    CloudJobCheckpoint.COMPLETED,
}


def get_cloud_job_store() -> CloudJobStore:
    return CloudJobStore(str(config.app["cloud_agent_db_path"]))


def get_cloud_job_storage() -> CloudJobStorage:
    return CloudJobStorage()


def get_cloud_agent_sessions() -> SessionManager:
    return build_session_manager()


def get_pre_flow_retry_service() -> PreFlowRetryService:
    return build_pre_flow_retry_service()


def _job_data(job) -> dict:
    return job.model_dump(mode="json")


def _require_job(store: CloudJobStore, job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HttpException(
            task_id=job_id,
            status_code=404,
            message=f"cloud agent job not found: {job_id}",
        )
    return job


def _invalid_transition(job_id: str, action: str, status: CloudJobStatus) -> None:
    raise HttpException(
        task_id=job_id,
        status_code=409,
        message=f"cannot {action} cloud agent job from status {status.value}",
    )


@router.get("/cloud-agent/health")
def get_cloud_agent_health(
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
    storage: CloudJobStorage = Depends(get_cloud_job_storage),
):
    del request
    storage_root = Path(storage.root)
    storage_writable = _probe_storage_writable(storage_root)
    free_space_bytes = 0
    if storage_writable:
        try:
            free_space_bytes = int(shutil.disk_usage(storage_root).free)
        except OSError:
            storage_writable = False

    required_free_bytes = int(
        float(config.app["cloud_agent_min_free_disk_gb"]) * 1024**3
    )
    worker_last_seen = store.get_worker_last_seen()
    return utils.get_response(
        200,
        {
            "enabled": bool(config.app["cloud_agent_enabled"]),
            "worker_last_seen": worker_last_seen or "",
            "worker_online": bool(worker_last_seen),
            "storage_writable": storage_writable,
            "free_space_bytes": free_space_bytes,
            "free_space_ok": storage_writable
            and free_space_bytes >= required_free_bytes,
        },
    )


@router.post("/cloud-agent/draft")
def create_cloud_agent_draft(request: Request, body: CloudJobDraftRequest):
    del request
    script = str(body.script or "").strip()
    if not script:
        script = generate_script(
            video_subject=body.subject,
            language=body.language,
            paragraph_number=1,
            video_script_prompt=(
                f"Write approximately {body.target_words} words for this narration."
            ),
        ).strip()
    if script.startswith("Error:"):
        raise HttpException(
            task_id="cloud-agent-draft",
            status_code=422,
            message=script.removeprefix("Error:").strip(),
        )
    if not script:
        raise HttpException(
            task_id="cloud-agent-draft",
            status_code=422,
            message="script generation returned no narration",
        )

    clip_plan = generate_six_clip_plan(
        script,
        language=body.language,
        target_words=body.target_words,
    )
    return utils.get_response(
        200,
        {
            "script": script,
            "master_prompt": build_master_prompt(clip_plan),
            "clip_plan": clip_plan.model_dump(mode="json"),
        },
    )


@router.post("/cloud-agent/jobs")
def create_cloud_agent_job(
    request: Request,
    body: CloudJobCreate,
    store: CloudJobStore = Depends(get_cloud_job_store),
):
    del request
    job = store.create_job(body)
    return utils.get_response(200, _job_data(job))


@router.get("/cloud-agent/jobs")
def list_cloud_agent_jobs(
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
):
    del request
    jobs = store.list_jobs()
    return utils.get_response(200, [_job_data(job) for job in jobs])


@router.get("/cloud-agent/jobs/{job_id}")
def get_cloud_agent_job(
    job_id: str,
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
):
    del request
    return utils.get_response(200, _job_data(_require_job(store, job_id)))


@router.post("/cloud-agent/jobs/{job_id}/pause")
def pause_cloud_agent_job(
    job_id: str,
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
):
    del request
    job = _require_job(store, job_id)
    if job.status is CloudJobStatus.PAUSED:
        return utils.get_response(200, _job_data(job))
    if job.status is CloudJobStatus.QUEUED:
        job = store.patch_job(
            job.id,
            status=CloudJobStatus.PAUSED,
            current_step="paused",
            control_request=CloudControlRequest.NONE,
        )
        return utils.get_response(200, _job_data(job))
    if job.status in _ACTIVE_JOB_STATUSES:
        job = store.patch_job(job.id, control_request=CloudControlRequest.PAUSE)
        return utils.get_response(200, _job_data(job))
    _invalid_transition(job.id, "pause", job.status)


@router.post("/cloud-agent/jobs/{job_id}/resume")
def resume_cloud_agent_job(
    job_id: str,
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
):
    del request
    job = _require_job(store, job_id)
    if job.status not in {CloudJobStatus.PAUSED, CloudJobStatus.HUMAN_REQUIRED}:
        _invalid_transition(job.id, "resume", job.status)
    job = store.patch_job(
        job.id,
        status=CloudJobStatus.QUEUED,
        current_step="queued",
        control_request=CloudControlRequest.NONE,
        error_code="",
        error_message="",
    )
    return utils.get_response(200, _job_data(job))


@router.post("/cloud-agent/jobs/{job_id}/retry")
def retry_cloud_agent_job(
    job_id: str,
    request: Request,
    retry_service: PreFlowRetryService = Depends(get_pre_flow_retry_service),
):
    del request
    try:
        job = retry_service.retry(job_id)
    except PreFlowRetryEligibilityError as exc:
        raise HttpException(
            task_id=job_id,
            status_code=409,
            message=str(exc),
        ) from exc
    return utils.get_response(200, _job_data(job))


@router.post("/cloud-agent/jobs/{job_id}/cancel")
def cancel_cloud_agent_job(
    job_id: str,
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
):
    del request
    job = _require_job(store, job_id)
    if job.status is CloudJobStatus.CANCELLED:
        return utils.get_response(200, _job_data(job))
    if job.status in {
        CloudJobStatus.QUEUED,
        CloudJobStatus.PAUSED,
        CloudJobStatus.HUMAN_REQUIRED,
    }:
        job = store.patch_job(
            job.id,
            status=CloudJobStatus.CANCELLED,
            current_step="cancelled",
            control_request=CloudControlRequest.NONE,
        )
        return utils.get_response(200, _job_data(job))
    if job.status in _ACTIVE_JOB_STATUSES:
        job = store.patch_job(job.id, control_request=CloudControlRequest.CANCEL)
        return utils.get_response(200, _job_data(job))
    if job.status in _TERMINAL_JOB_STATUSES:
        _invalid_transition(job.id, "cancel", job.status)
    _invalid_transition(job.id, "cancel", job.status)


@router.get("/cloud-agent/jobs/{job_id}/final")
def get_cloud_agent_final(
    job_id: str,
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
    storage: CloudJobStorage = Depends(get_cloud_job_storage),
):
    del request
    job = _require_job(store, job_id)
    if job.checkpoint not in _FINAL_CHECKPOINTS:
        raise HttpException(
            task_id=job.id,
            status_code=409,
            message="cloud agent final video has not passed final validation",
        )

    paths = storage.prepare(job.id)
    if not job.final_video:
        raise HttpException(
            task_id=job.id,
            status_code=404,
            message="cloud agent final video is unavailable",
        )

    expected = paths.final_file.resolve()
    recorded = Path(job.final_video).resolve()
    if recorded != expected:
        raise HttpException(
            task_id=job.id,
            status_code=409,
            message="cloud agent final video path does not match the job artifact",
        )

    try:
        resolved = Path(
            resolve_path_within_directory(
                str(paths.job_dir),
                str(paths.final_file),
                require_file=True,
            )
        )
    except ValueError as exc:
        raise HttpException(
            task_id=job.id,
            status_code=404,
            message="cloud agent final video is unavailable",
        ) from exc

    if resolved != expected:
        raise HttpException(
            task_id=job.id,
            status_code=409,
            message="cloud agent final video path is outside the job directory",
        )

    return FileResponse(
        path=str(resolved),
        media_type="video/mp4",
        filename=f"{job.id}.mp4",
    )


@router.post("/cloud-agent/sessions/check")
def check_cloud_agent_sessions(
    request: Request,
    sessions: SessionManager = Depends(get_cloud_agent_sessions),
):
    del request
    return utils.get_response(
        200,
        {service: result.model_dump(mode="json") for service, result in sessions.check_all().items()},
    )


@router.post("/cloud-agent/sessions/google-flow/check")
def check_google_flow_session(
    request: Request,
    sessions: SessionManager = Depends(get_cloud_agent_sessions),
):
    del request
    return utils.get_response(200, _session_data(sessions, "google_flow"))


@router.post("/cloud-agent/sessions/canva/check")
def check_canva_session(
    request: Request,
    sessions: SessionManager = Depends(get_cloud_agent_sessions),
):
    del request
    return utils.get_response(200, _session_data(sessions, "canva"))


@router.post("/cloud-agent/sessions/google-flow/repair")
def repair_google_flow_session(
    request: Request,
    sessions: SessionManager = Depends(get_cloud_agent_sessions),
):
    del request
    return utils.get_response(200, _repair_session_data(sessions, "google_flow"))


@router.post("/cloud-agent/sessions/canva/repair")
def repair_canva_session(
    request: Request,
    sessions: SessionManager = Depends(get_cloud_agent_sessions),
):
    del request
    return utils.get_response(200, _repair_session_data(sessions, "canva"))


@router.get("/cloud-agent/sessions/{service}/open-browser")
def open_cloud_agent_browser(service: str, request: Request):
    del request
    if service not in {"google_flow", "canva"}:
        raise HttpException(task_id="", status_code=400, message="unsupported cloud agent service")
    return utils.get_response(200, {"url": config.app["cloud_agent_remote_browser_url"]})


def _session_data(sessions: SessionManager, service: str) -> dict:
    try:
        result = sessions.providers[service].check_session()
    except KeyError as exc:
        raise HttpException(task_id="", status_code=400, message="unsupported cloud agent service") from exc
    return result.model_dump(mode="json")


def _repair_session_data(sessions: SessionManager, service: str) -> dict:
    try:
        return sessions.ensure_service_ready(service, job_id="").model_dump(mode="json")
    except HumanRequiredError as exc:
        return {"service": service, "status": "HUMAN_REQUIRED", "message": str(exc)}
    except ValueError as exc:
        raise HttpException(task_id="", status_code=400, message="unsupported cloud agent service") from exc
