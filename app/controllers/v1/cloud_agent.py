from fastapi import Depends, Request

from app.config import config
from app.controllers.v1.base import new_router
from app.models.cloud_agent import CloudJobCreate
from app.models.exception import HttpException
from app.services.cloud_agent.job_store import CloudJobStore
from app.utils import utils


router = new_router()


def get_cloud_job_store() -> CloudJobStore:
    return CloudJobStore(str(config.app["cloud_agent_db_path"]))


def _not_implemented():
    raise NotImplementedError("cloud agent API behavior is implemented incrementally via TDD")


def _job_data(job) -> dict:
    return job.model_dump(mode="json")


@router.get("/cloud-agent/health")
def get_cloud_agent_health(request: Request):
    del request
    return _not_implemented()


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
    job = store.get_job(job_id)
    if job is None:
        raise HttpException(
            task_id=job_id,
            status_code=404,
            message=f"cloud agent job not found: {job_id}",
        )
    return utils.get_response(200, _job_data(job))


@router.post("/cloud-agent/jobs/{job_id}/pause")
def pause_cloud_agent_job(job_id: str, request: Request):
    del job_id, request
    return _not_implemented()


@router.post("/cloud-agent/jobs/{job_id}/resume")
def resume_cloud_agent_job(job_id: str, request: Request):
    del job_id, request
    return _not_implemented()


@router.post("/cloud-agent/jobs/{job_id}/cancel")
def cancel_cloud_agent_job(job_id: str, request: Request):
    del job_id, request
    return _not_implemented()


@router.get("/cloud-agent/jobs/{job_id}/final")
def get_cloud_agent_final(job_id: str, request: Request):
    del job_id, request
    return _not_implemented()


@router.post("/cloud-agent/sessions/check")
def check_cloud_agent_sessions(request: Request):
    del request
    return _not_implemented()


@router.post("/cloud-agent/sessions/google-flow/check")
def check_google_flow_session(request: Request):
    del request
    return _not_implemented()


@router.post("/cloud-agent/sessions/canva/check")
def check_canva_session(request: Request):
    del request
    return _not_implemented()


@router.post("/cloud-agent/sessions/google-flow/repair")
def repair_google_flow_session(request: Request):
    del request
    return _not_implemented()


@router.post("/cloud-agent/sessions/canva/repair")
def repair_canva_session(request: Request):
    del request
    return _not_implemented()


@router.get("/cloud-agent/sessions/{service}/open-browser")
def open_cloud_agent_browser(service: str, request: Request):
    del service, request
    return _not_implemented()
