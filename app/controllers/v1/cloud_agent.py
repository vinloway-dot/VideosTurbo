from fastapi import Request

from app.controllers.v1.base import new_router


router = new_router()


def _not_implemented():
    raise NotImplementedError("cloud agent API behavior is implemented incrementally via TDD")


@router.get("/cloud-agent/health")
def get_cloud_agent_health(request: Request):
    del request
    return _not_implemented()


@router.post("/cloud-agent/jobs")
def create_cloud_agent_job(request: Request):
    del request
    return _not_implemented()


@router.get("/cloud-agent/jobs")
def list_cloud_agent_jobs(request: Request):
    del request
    return _not_implemented()


@router.get("/cloud-agent/jobs/{job_id}")
def get_cloud_agent_job(job_id: str, request: Request):
    del job_id, request
    return _not_implemented()


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
