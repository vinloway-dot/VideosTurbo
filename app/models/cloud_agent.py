from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class FlowRecoveryState(str, Enum):
    NONE = "NONE"
    INVENTORY_PENDING = "INVENTORY_PENDING"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMISSION_UNRESOLVED = "SUBMISSION_UNRESOLVED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"


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
    research_draft_id: str = Field(default="", exclude=True, max_length=64)
    language: str = ""
    target_words: int = Field(default=130, ge=40, le=400)
    tts_provider: str
    voice_id: str
    voice_speed: float = Field(default=1.0, gt=0)
    prepared_voice_fingerprint: str = Field(default="", exclude=True, max_length=64)

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


class CloudDraftVoiceRequest(BaseModel):
    script: str
    tts_provider: str
    voice_id: str
    voice_speed: float = Field(default=1.0, gt=0)

    @field_validator("script", "tts_provider", "voice_id")
    @classmethod
    def _require_non_blank_content(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class CloudDraftVoiceArtifact(BaseModel):
    fingerprint: str
    reused: bool
    filename: str = "voice.mp3"


class CloudAgentDefaults(BaseModel):
    tts_provider: str
    voice_id: str = ""
    voice_speed: float = Field(default=1.0, gt=0)
    custom_system_prompt: str = Field(default="", max_length=8000)


class CloudAgentDefaultsPatch(CloudAgentDefaults):
    @field_validator("tts_provider")
    @classmethod
    def _require_provider(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("tts_provider must not be blank")
        return normalized


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
    last_progress_at: str = ""
    last_progress_milestone: str = ""
    stage_started_at: str = ""
    flow_recovery_attempts: int = Field(default=0, ge=0, le=2)
    flow_workspace_retry_attempts: int = Field(default=0, ge=0, le=2)
    flow_workspace_retry_not_before: str = ""
    flow_missing_clip_index: int = Field(default=0, ge=0, le=6)
    flow_recovery_state: FlowRecoveryState = FlowRecoveryState.NONE
    flow_recovery_baseline: str = ""
    canva_restart_attempts: int = Field(default=0, ge=0, le=4)
    canva_attempt_started_at: str = ""
    final_video: str
    error_code: str
    error_message: str
    worker_id: str
    lease_until: str
    created_at: str
    started_at: str
    completed_at: str
    updated_at: str


class CloudJobIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    former_job_id: str = Field(min_length=1, max_length=64)
    subject: str = Field(max_length=500)
    stage: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=64)
    flow_attempts: int = Field(ge=0, le=2)
    canva_attempts: int = Field(ge=0, le=4)
    message_th: str = Field(min_length=1, max_length=1000)
    created_at: str
    dismissed_at: str = ""
    finalized: bool = False


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


class TTSVoiceOption(BaseModel):
    id: str
    label: str


class TTSSettingField(BaseModel):
    name: str
    label: str
    kind: Literal["text", "password", "select", "voice_list"]
    value: str | list[str] | None = None
    configured: bool = False
    choices: list[str] = Field(default_factory=list)


class TTSProviderMetadata(BaseModel):
    id: str
    label: str
    voices: list[TTSVoiceOption]
    settings: list[TTSSettingField]
    requires_explicit_voice_refresh: bool = False


class TTSProviderSettingsPatch(BaseModel):
    settings: dict[str, str | list[str]] = Field(default_factory=dict)
    clear_secret_fields: list[str] = Field(default_factory=list)
