from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.six_clip import SixClipPlan


class CloudJobStatus(str, Enum):
    DRAFT = "DRAFT"
    SCRIPT_READY = "SCRIPT_READY"
    PROMPT_READY = "PROMPT_READY"
    QUEUED = "QUEUED"
    PREFLIGHT = "PREFLIGHT"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    TTS_GENERATING = "TTS_GENERATING"
    TTS_READY = "TTS_READY"
    FLOW_GENERATING = "FLOW_GENERATING"
    FLOW_DOWNLOADING = "FLOW_DOWNLOADING"
    FLOW_READY = "FLOW_READY"
    CANVA_UPLOADING = "CANVA_UPLOADING"
    CANVA_EDITING = "CANVA_EDITING"
    CAPTIONING = "CAPTIONING"
    EXPORTING = "EXPORTING"
    DOWNLOADING_FINAL = "DOWNLOADING_FINAL"
    VALIDATING = "VALIDATING"
    FINAL_VALIDATED = "FINAL_VALIDATED"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CloudJobCheckpoint(str, Enum):
    NONE = "NONE"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    TTS_READY = "TTS_READY"
    FLOW_READY = "FLOW_READY"
    FINAL_VALIDATED = "FINAL_VALIDATED"
    COMPLETED = "COMPLETED"


class CloudControlRequest(str, Enum):
    NONE = "NONE"
    PAUSE = "PAUSE"
    CANCEL = "CANCEL"


class ServiceSessionStatus(str, Enum):
    CHECKING = "CHECKING"
    READY = "READY"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    AUTO_RELOGIN = "AUTO_RELOGIN"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    TWO_FACTOR_REQUIRED = "2FA_REQUIRED"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    ERROR = "ERROR"


class CloudJobCreate(BaseModel):
    subject: str
    script: str
    master_prompt: str
    clip_plan: SixClipPlan
    language: str = ""
    target_words: int = Field(default=130, ge=40, le=400)
    tts_provider: str
    voice_id: str
    voice_speed: float = Field(default=1.0, gt=0)

    @field_validator("script", "master_prompt")
    @classmethod
    def _require_non_blank_content(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_target_words_match_clip_plan(self):
        if self.target_words != self.clip_plan.target_words:
            raise ValueError("target_words must match clip_plan.target_words")
        return self


class CloudJobDraftRequest(BaseModel):
    subject: str
    script: str = ""
    language: str = ""
    target_words: int = Field(default=130, ge=40, le=400)
    custom_system_prompt: str = Field(default="", max_length=8000)

    @field_validator("subject")
    @classmethod
    def _require_subject(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class CloudJobRecord(CloudJobCreate):
    id: str
    status: CloudJobStatus
    checkpoint: CloudJobCheckpoint
    control_request: CloudControlRequest
    current_step: str
    progress: int = Field(ge=0, le=100)
    flow_status: str
    canva_status: str
    voice_file: str
    audio_duration_seconds: float = Field(default=0.0, ge=0)
    canva_playback_speed: float = Field(default=1.0, gt=0, le=1.0)
    target_final_duration_seconds: float = Field(default=60.0, gt=0)
    flow_generation_unresolved: bool = False
    flow_cleanup_unresolved: bool = False
    canva_design_url: str = ""
    canva_audio_card_count: int = -1
    final_video: str
    error_code: str
    error_message: str
    worker_id: str
    lease_until: str
    created_at: str
    started_at: str
    completed_at: str
    updated_at: str


class SessionCheckResult(BaseModel):
    service: str
    status: ServiceSessionStatus
    message: str = ""
    checked_at: str
    evidence_path: str = ""


class CloudAgentHealth(BaseModel):
    enabled: bool
    worker_online: bool
    worker_last_seen: str = ""
    storage_writable: bool
    free_space_ok: bool
    free_space_bytes: int = Field(ge=0)
