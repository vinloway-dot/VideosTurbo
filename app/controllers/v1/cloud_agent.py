import json
import math
import shutil
from pathlib import Path
from typing import Callable

from fastapi import Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.config import config
from app.controllers.v1.base import new_router
from app.models.cloud_agent import (
    CloudAgentDefaultsPatch,
    CloudControlRequest,
    CloudDraftVoiceRequest,
    CloudJobDraftRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
    TTSProviderSettingsPatch,
)
from app.services.llm import generate_script
from app.services.six_clip_plan import build_master_prompt, generate_six_clip_plan
from app.models.exception import HttpException
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.errors import HumanRequiredError, MediaValidationError
from app.services.cloud_agent.errors import PreFlowRetryEligibilityError
from app.services.cloud_agent.factory import (
    build_pre_flow_retry_service,
    build_session_manager,
    build_cloud_tts_settings_service,
    build_research_draft_store,
    build_research_script_service,
    build_research_settings_service,
    build_draft_voice_service,
    build_cloud_agent_defaults_service,
)
from app.services.cloud_agent.draft_voice import DraftVoiceError, DraftVoiceService
from app.services.cloud_agent.defaults import CloudAgentDefaultsError, CloudAgentDefaultsService
from app.services.cloud_agent.retry import PreFlowRetryService
from app.services.cloud_agent.preflight import _probe_storage_writable
from app.services.cloud_agent.research import (
    ResearchDraftRequest,
    ResearchError,
    public_research_message,
)
from app.services.cloud_agent.research.service import ResearchScriptService
from app.services.cloud_agent.research.settings import ResearchSettingsService
from app.services.cloud_agent.research.store import ResearchDraftStore
from app.services.cloud_agent.session import SessionManager
from app.services.cloud_agent.tts_settings import (
    CloudTTSSettingsError,
    CloudTTSSettingsService,
)
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
_RESEARCH_SETTINGS_KEYS = {
    "enabled": "cloud_agent_research_enabled",
    "provider": "cloud_agent_research_default_provider",
    "openrouter_model": "cloud_agent_research_openrouter_model",
    "openrouter_custom_model_id": "cloud_agent_research_openrouter_custom_model",
    "aihubmix_model": "cloud_agent_research_aihubmix_model",
    "aihubmix_custom_model_id": "cloud_agent_research_aihubmix_custom_model",
    "custom_system_prompt": "cloud_agent_research_custom_system_prompt",
}
_RESEARCH_HTTP_STATUS = {
    "PROVIDER_API_KEY_MISSING": 422,
    "PROVIDER_AUTHENTICATION_FAILED": 401,
    "PROVIDER_TIMEOUT": 504,
    "URL_FETCH_FAILED": 502,
}


class ResearchSettingsPayload(BaseModel):
    enabled: bool | None = None
    provider: str
    openrouter_model: str = Field(max_length=256)
    openrouter_custom_model_id: str = Field(default="", max_length=256)
    aihubmix_model: str = Field(max_length=256)
    aihubmix_custom_model_id: str = Field(default="", max_length=256)
    custom_system_prompt: str = Field(default="", max_length=8000)

    @field_validator(
        "provider",
        "openrouter_model",
        "openrouter_custom_model_id",
        "aihubmix_model",
        "aihubmix_custom_model_id",
        "custom_system_prompt",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()


class ResearchAPIKeyPatch(BaseModel):
    api_key: str = Field(default="")

    @field_validator("api_key")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()


class ConfirmResearchKeyRemoval(BaseModel):
    confirmed: bool


def get_cloud_job_store() -> CloudJobStore:
    return CloudJobStore(str(config.app["cloud_agent_db_path"]))


def get_cloud_job_storage() -> CloudJobStorage:
    return CloudJobStorage()


def get_cloud_agent_sessions() -> SessionManager:
    return build_session_manager()


def get_pre_flow_retry_service() -> PreFlowRetryService:
    return build_pre_flow_retry_service()


def get_cloud_tts_settings_service() -> CloudTTSSettingsService:
    return build_cloud_tts_settings_service()


def get_draft_voice_service() -> DraftVoiceService:
    return build_draft_voice_service()


def get_cloud_agent_defaults_service() -> CloudAgentDefaultsService:
    return build_cloud_agent_defaults_service()


def get_research_service() -> ResearchScriptService:
    _require_research_enabled()
    return build_research_script_service()


def get_research_settings_service() -> ResearchSettingsService:
    return build_research_settings_service()


def get_research_draft_store() -> ResearchDraftStore:
    return build_research_draft_store()


def get_research_draft_store_factory() -> Callable[[], ResearchDraftStore]:
    return get_research_draft_store


def _job_data(job) -> dict:
    return job.model_dump(mode="json")


def _cloud_tts_provider_data(
    service: CloudTTSSettingsService, provider_id: str
) -> dict:
    try:
        return service.get_provider(provider_id).model_dump(mode="json")
    except CloudTTSSettingsError as exc:
        raise HttpException(task_id="", status_code=422, message=str(exc)) from exc


def _require_job(store: CloudJobStore, job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HttpException(
            task_id=job_id,
            status_code=404,
            message=f"cloud agent job not found: {job_id}",
        )
    return job


def _research_http_status(code: str) -> int:
    return _RESEARCH_HTTP_STATUS.get(str(code or "").strip(), 422)


def _safe_accounting(value) -> dict:
    usage = getattr(value, "usage", {})
    if usage is None:
        usage = {}
    safe_usage = {
        str(key): number
        for key, number in dict(usage).items()
        if isinstance(number, int | float)
        and math.isfinite(number)
        and number >= 0
    }
    cost = getattr(value, "cost", None)
    if not isinstance(cost, int | float) or not math.isfinite(cost) or cost < 0:
        cost = None

    def safe_count(name: str) -> int:
        raw_value = getattr(value, name, 0)
        if (
            not isinstance(raw_value, int | float)
            or not math.isfinite(raw_value)
            or raw_value < 0
        ):
            return 0
        return int(raw_value)

    return {
        "tool_calls": safe_count("tool_calls"),
        "provider_rounds": safe_count("provider_rounds"),
        "usage": safe_usage,
        "cost": float(cost) if cost is not None else None,
    }


def _research_http_exception(exc: ResearchError) -> HttpException:
    return HttpException(
        task_id="cloud-agent-research",
        status_code=_research_http_status(exc.code),
        message=public_research_message(exc.code),
        data={
            "code": exc.code,
            "accounting": _safe_accounting(getattr(exc, "accounting", None)),
        },
    )


def _research_settings_data() -> dict:
    return {
        "enabled": bool(config.app.get(_RESEARCH_SETTINGS_KEYS["enabled"], False)),
        **{
            name: str(config.app.get(key, "") or "").strip()
            for name, key in _RESEARCH_SETTINGS_KEYS.items()
            if name != "enabled"
        },
    }


def _require_research_enabled() -> None:
    if bool(config.app.get("cloud_agent_research_enabled", False)):
        return
    raise HttpException(
        task_id="cloud-agent-research",
        status_code=404,
        message="Research Script is disabled.",
    )


def _validated_research_provider(
    provider_id: str, service: ResearchSettingsService
) -> str:
    return service.get_provider(provider_id).id


def _update_research_settings(
    body: ResearchSettingsPayload,
    service: ResearchSettingsService,
) -> dict:
    provider_id = _validated_research_provider(body.provider, service)
    openrouter_model = service.validate_model_choice(
        "openrouter",
        body.openrouter_model,
    )
    aihubmix_model = service.validate_model_choice(
        "aihubmix",
        body.aihubmix_model,
    )
    with config.runtime_config_lock():
        if body.enabled is not None:
            config.app[_RESEARCH_SETTINGS_KEYS["enabled"]] = body.enabled
        config.app[_RESEARCH_SETTINGS_KEYS["provider"]] = provider_id
        config.app[_RESEARCH_SETTINGS_KEYS["openrouter_model"]] = openrouter_model
        config.app[_RESEARCH_SETTINGS_KEYS["openrouter_custom_model_id"]] = (
            body.openrouter_custom_model_id
        )
        config.app[_RESEARCH_SETTINGS_KEYS["aihubmix_model"]] = aihubmix_model
        config.app[_RESEARCH_SETTINGS_KEYS["aihubmix_custom_model_id"]] = (
            body.aihubmix_custom_model_id
        )
        config.app[_RESEARCH_SETTINGS_KEYS["custom_system_prompt"]] = (
            body.custom_system_prompt
        )
        config.save_config()
    return _research_settings_data()


def _parse_research_api_key(body: object) -> str:
    if not isinstance(body, dict):
        raise ResearchError("RESEARCH_RESPONSE_INVALID", "invalid api key payload")
    value = body.get("api_key", "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ResearchError("RESEARCH_RESPONSE_INVALID", "api key must be a string")
    normalized = value.strip()
    if len(normalized) > 4096:
        raise ResearchError("RESEARCH_RESPONSE_INVALID", "api key exceeded limit")
    return normalized


async def _parse_research_api_key_request(request: Request) -> str:
    try:
        raw_body = await request.body()
    except Exception as exc:
        raise ResearchError("RESEARCH_RESPONSE_INVALID", "request body unavailable") from exc

    if not raw_body:
        parsed = {}
    else:
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "invalid api key payload") from exc
    return _parse_research_api_key(parsed)


def _research_association_failed(
    store: CloudJobStore, job_id: str, exc: Exception
) -> HttpException:
    failed_job = store.patch_job(
        job_id,
        status=CloudJobStatus.FAILED,
        current_step="research_draft_association_failed",
        error_code="RESEARCH_DRAFT_ASSOCIATION_FAILED",
        error_message="research draft association failed",
    )
    return HttpException(
        task_id=failed_job.id,
        status_code=422,
        message=public_research_message("RESEARCH_RESPONSE_INVALID"),
        data={
            "code": "RESEARCH_DRAFT_ASSOCIATION_FAILED",
            "accounting": _safe_accounting(None),
        },
    )


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


@router.get("/cloud-agent/tts/providers")
def list_cloud_tts_providers(
    request: Request,
    service: CloudTTSSettingsService = Depends(get_cloud_tts_settings_service),
):
    del request
    return utils.get_response(
        200, [item.model_dump(mode="json") for item in service.list_providers()]
    )


@router.get("/cloud-agent/tts/providers/{provider_id}")
def get_cloud_tts_provider(
    provider_id: str,
    request: Request,
    service: CloudTTSSettingsService = Depends(get_cloud_tts_settings_service),
):
    del request
    return utils.get_response(200, _cloud_tts_provider_data(service, provider_id))


@router.put("/cloud-agent/tts/providers/{provider_id}/settings")
def update_cloud_tts_provider_settings(
    provider_id: str,
    body: TTSProviderSettingsPatch,
    request: Request,
    service: CloudTTSSettingsService = Depends(get_cloud_tts_settings_service),
):
    del request
    try:
        data = service.update_provider(provider_id, body).model_dump(mode="json")
    except CloudTTSSettingsError as exc:
        raise HttpException(task_id="", status_code=422, message=str(exc)) from exc
    return utils.get_response(200, data)


@router.post("/cloud-agent/tts/providers/{provider_id}/voices/refresh")
def refresh_cloud_tts_provider_voices(
    provider_id: str,
    request: Request,
    service: CloudTTSSettingsService = Depends(get_cloud_tts_settings_service),
):
    del request
    try:
        data = service.refresh_voices(provider_id).model_dump(mode="json")
    except CloudTTSSettingsError as exc:
        raise HttpException(task_id="", status_code=422, message=str(exc)) from exc
    return utils.get_response(200, data)


@router.get("/cloud-agent/defaults")
def get_cloud_agent_defaults(
    request: Request,
    service: CloudAgentDefaultsService = Depends(get_cloud_agent_defaults_service),
):
    del request
    return utils.get_response(200, service.get().model_dump(mode="json"))


@router.put("/cloud-agent/defaults")
def update_cloud_agent_defaults(
    body: CloudAgentDefaultsPatch,
    request: Request,
    service: CloudAgentDefaultsService = Depends(get_cloud_agent_defaults_service),
):
    del request
    try:
        data = service.update(body).model_dump(mode="json")
    except CloudAgentDefaultsError as exc:
        raise HttpException(
            task_id="cloud-agent-defaults", status_code=422, message=str(exc)
        ) from exc
    return utils.get_response(200, data)


@router.post("/cloud-agent/defaults/reset")
def reset_cloud_agent_defaults(
    request: Request,
    service: CloudAgentDefaultsService = Depends(get_cloud_agent_defaults_service),
):
    del request
    return utils.get_response(200, service.reset().model_dump(mode="json"))


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
            custom_system_prompt=body.custom_system_prompt,
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


@router.post("/cloud-agent/draft/voice")
def create_cloud_agent_draft_voice(
    body: CloudDraftVoiceRequest,
    request: Request,
    voices: DraftVoiceService = Depends(get_draft_voice_service),
):
    del request
    try:
        artifact = voices.prepare(body)
    except DraftVoiceError as exc:
        raise HttpException(task_id="cloud-agent-draft-voice", status_code=422, message=str(exc)) from exc
    return utils.get_response(200, artifact.model_dump(mode="json"))


@router.get("/cloud-agent/draft/voices/{fingerprint}/audio")
def get_cloud_agent_draft_voice_audio(
    fingerprint: str,
    request: Request,
    voices: DraftVoiceService = Depends(get_draft_voice_service),
):
    del request
    try:
        path = voices.get(fingerprint)
    except DraftVoiceError as exc:
        raise HttpException(task_id="cloud-agent-draft-voice", status_code=404, message=str(exc)) from exc
    return FileResponse(path=str(path), media_type="audio/mpeg", filename="voice.mp3")


@router.get("/cloud-agent/research/providers")
def list_research_providers(
    request: Request,
    service: ResearchSettingsService = Depends(get_research_settings_service),
):
    del request
    return utils.get_response(
        200, [item.model_dump(mode="json") for item in service.list_providers()]
    )


@router.get("/cloud-agent/research/settings")
def get_research_settings(
    request: Request,
    service: ResearchSettingsService = Depends(get_research_settings_service),
):
    del request, service
    return utils.get_response(200, _research_settings_data())


@router.put("/cloud-agent/research/settings")
def update_research_settings(
    body: ResearchSettingsPayload,
    request: Request,
    service: ResearchSettingsService = Depends(get_research_settings_service),
):
    del request
    try:
        data = _update_research_settings(body, service)
    except ResearchError as exc:
        raise _research_http_exception(exc) from exc
    return utils.get_response(200, data)


@router.put("/cloud-agent/research/providers/{provider_id}/api-key")
async def update_research_provider_api_key(
    provider_id: str,
    request: Request,
    service: ResearchSettingsService = Depends(get_research_settings_service),
):
    try:
        data = service.set_api_key(
            provider_id, await _parse_research_api_key_request(request)
        ).model_dump(mode="json")
    except ResearchError as exc:
        raise _research_http_exception(exc) from exc
    return utils.get_response(200, data)


@router.delete("/cloud-agent/research/providers/{provider_id}/api-key")
def delete_research_provider_api_key(
    provider_id: str,
    body: ConfirmResearchKeyRemoval,
    request: Request,
    service: ResearchSettingsService = Depends(get_research_settings_service),
):
    del request
    try:
        data = service.remove_api_key(provider_id, body.confirmed).model_dump(
            mode="json"
        )
    except ResearchError as exc:
        raise _research_http_exception(exc) from exc
    return utils.get_response(200, data)


@router.post("/cloud-agent/research/drafts")
def create_research_draft(
    body: ResearchDraftRequest,
    request: Request,
    service: ResearchScriptService = Depends(get_research_service),
):
    del request
    try:
        _require_research_enabled()
        data = service.create_draft(body).model_dump(mode="json")
    except ResearchError as exc:
        raise _research_http_exception(exc) from exc
    return utils.get_response(200, data)


@router.get("/cloud-agent/research/drafts/{research_draft_id}")
def get_research_draft(
    research_draft_id: str,
    request: Request,
    store: ResearchDraftStore = Depends(get_research_draft_store),
):
    del request
    draft = store.get(research_draft_id)
    if draft is None:
        raise HttpException(
            task_id="cloud-agent-research",
            status_code=404,
            message=f"research draft not found: {research_draft_id}",
        )
    return utils.get_response(200, draft.model_dump(mode="json"))


@router.post("/cloud-agent/jobs")
def create_cloud_agent_job(
    request: Request,
    body: CloudJobCreate,
    store: CloudJobStore = Depends(get_cloud_job_store),
    storage: CloudJobStorage = Depends(get_cloud_job_storage),
    voices: DraftVoiceService = Depends(get_draft_voice_service),
    research_store_factory: Callable[[], ResearchDraftStore] = Depends(
        get_research_draft_store_factory
    ),
):
    del request
    prepared_voice = str(body.prepared_voice_fingerprint or "").strip()
    research_draft_id = str(body.research_draft_id or "").strip()
    research_store = None
    if prepared_voice:
        try:
            voices.get(prepared_voice)
        except DraftVoiceError as exc:
            raise HttpException(task_id="cloud-agent-job", status_code=422, message=str(exc)) from exc
    if research_draft_id:
        try:
            research_store = research_store_factory()
            research_store.assert_script_matches(research_draft_id, body.script)
        except ResearchError as exc:
            raise _research_http_exception(exc) from exc

    job = store.create_job(body, status=CloudJobStatus.DRAFT)
    if prepared_voice:
        try:
            paths = storage.prepare(job.id)
            voices.materialize(prepared_voice, paths.voice_file)
            job = store.patch_job(job.id, voice_file=str(paths.voice_file))
        except (DraftVoiceError, MediaValidationError, OSError) as exc:
            job = store.patch_job(
                job.id,
                status=CloudJobStatus.FAILED,
                current_step="prepared_voice_failed",
                error_code="PREPARED_VOICE_UNAVAILABLE",
                error_message="prepared narration could not be materialized",
            )
            raise HttpException(task_id=job.id, status_code=422, message="prepared narration could not be materialized") from exc
    if research_draft_id:
        try:
            if research_store is None:
                research_store = research_store_factory()
            research_store.link_job(research_draft_id, job.id)
        except Exception as exc:
            raise _research_association_failed(store, job.id, exc) from exc
    job = store.patch_job(job.id, status=CloudJobStatus.QUEUED, current_step="queued")
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
