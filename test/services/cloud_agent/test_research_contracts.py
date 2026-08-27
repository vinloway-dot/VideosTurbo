from app.models.cloud_agent import CloudJobCreate
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.research.errors import (
    ResearchError,
    public_research_message,
)
from app.services.cloud_agent.research.models import ResearchDraftRequest


def valid_job_payload() -> dict:
    return {
        "subject": "Task 1 contract",
        "script": "A valid narration script for research contract coverage.",
        "master_prompt": "Create six chronological videos from this narration.",
        "clip_plan": empty_six_clip_plan(target_words=130).model_dump(mode="json"),
        "language": "",
        "target_words": 130,
        "tts_provider": "azure-tts-v1",
        "voice_id": "en-US-JennyNeural-Female",
        "voice_speed": 1.0,
    }


def test_research_request_preserves_urls_for_domain_preflight():
    request = ResearchDraftRequest(
        subject="topic",
        language="",
        target_words=130,
        provider="openrouter",
        model_choice="openai/gpt-5.6-sol-pro",
        custom_model_id="",
        source_urls=[],
        custom_system_prompt="",
    )

    assert request.source_urls == []


def test_error_exposes_code_but_not_internal_detail():
    error = ResearchError("URL_TARGET_NOT_PUBLIC", "socket connected to 127.0.0.1")

    assert error.code == "URL_TARGET_NOT_PUBLIC"
    assert "127.0.0.1" not in str(error)
    assert public_research_message(error.code) == "URL นี้ไม่ใช่ปลายทางสาธารณะที่อนุญาต"


def test_job_research_id_is_excluded_from_workflow_payload():
    request = CloudJobCreate(**valid_job_payload(), research_draft_id="draft-1")

    assert "research_draft_id" not in request.model_dump(mode="json")
