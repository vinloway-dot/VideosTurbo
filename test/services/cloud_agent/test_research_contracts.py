from app.models.cloud_agent import CloudJobCreate
from app.models.six_clip import empty_six_clip_plan
from app.config.config import RESEARCH_DEFAULTS
from app.services.cloud_agent.research.errors import (
    RESEARCH_ERROR_CODES,
    RESEARCH_PUBLIC_MESSAGES,
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


def test_research_request_preserves_urls_and_defaults_citations_off():
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
    assert request.allow_citations is False
    assert request.model_dump(mode="json")["allow_citations"] is False

    enabled = request.model_copy(update={"allow_citations": True})

    assert enabled.allow_citations is True


def test_error_exposes_code_but_not_internal_detail():
    error = ResearchError("URL_TARGET_NOT_PUBLIC", "socket connected to 127.0.0.1")

    assert error.code == "URL_TARGET_NOT_PUBLIC"
    assert "127.0.0.1" not in str(error)
    assert public_research_message(error.code) == "URL นี้ไม่ใช่ปลายทางสาธารณะที่อนุญาต"


def test_job_research_id_is_excluded_from_workflow_payload():
    request = CloudJobCreate(**valid_job_payload(), research_draft_id="draft-1")

    assert "research_draft_id" not in request.model_dump(mode="json")


def test_research_defaults_are_exact_and_independently_disabled():
    assert RESEARCH_DEFAULTS == {
        "cloud_agent_research_enabled": False,
        "cloud_agent_research_default_provider": "aihubmix",
        "cloud_agent_research_openrouter_model": "openai/gpt-5.6-sol-pro",
        "cloud_agent_research_openrouter_custom_model": "openai/gpt-5.6-sol-pro",
        "cloud_agent_research_aihubmix_model": "gpt-5.6-sol",
        "cloud_agent_research_aihubmix_custom_model": "gpt-5.6-sol",
        "cloud_agent_research_custom_system_prompt": "",
        "cloud_agent_research_openrouter_api_key": "",
        "cloud_agent_research_aihubmix_api_key": "",
    }


def test_research_error_code_and_public_message_inventories_are_exact():
    expected_messages = {
        "URL_REQUIRED": "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง",
        "URL_INVALID": "URL ไม่ถูกต้อง กรุณาตรวจสอบลิงก์และใส่ได้สูงสุด 3 แหล่ง",
        "URL_TARGET_NOT_PUBLIC": "URL นี้ไม่ใช่ปลายทางสาธารณะที่อนุญาต",
        "URL_REDIRECT_REJECTED": "URL เปลี่ยนเส้นทางไปยังปลายทางที่ไม่อนุญาต",
        "URL_FETCH_FAILED": "ไม่สามารถอ่านหน้าเว็บนี้ได้ กรุณาตรวจสอบว่าเปิดสาธารณะและลองใหม่",
        "URL_CONTENT_UNSUPPORTED": "แหล่งนี้ไม่ใช่หน้าเว็บหรือ PDF ที่ระบบรองรับ",
        "URL_CONTENT_TOO_LARGE": "หน้าเว็บนี้มีขนาดเกินขีดจำกัดความปลอดภัย",
        "PDF_INVALID": "ไฟล์ PDF ไม่ถูกต้องหรือเปิดอ่านไม่ได้",
        "PDF_TOO_LARGE": "ไฟล์ PDF มีขนาดหรือจำนวนหน้าเกินขีดจำกัดความปลอดภัย",
        "PDF_TEXT_UNAVAILABLE": "ไม่พบข้อความที่อ่านได้ใน PDF นี้",
        "SOURCE_EVIDENCE_EMPTY": "ไม่พบข้อมูลที่อ่านได้จากแหล่งที่ให้มา",
        "SOURCE_CONTEXT_TOO_LARGE": "ข้อมูลจากแหล่งอ้างอิงยาวเกินขีดจำกัดของโมเดลที่เลือก",
        "PROVIDER_API_KEY_MISSING": "ยังไม่ได้ตั้งค่า API key ของผู้ให้บริการที่เลือก",
        "PROVIDER_AUTHENTICATION_FAILED": "API key ของผู้ให้บริการไม่ถูกต้องหรือใช้งานไม่ได้",
        "PROVIDER_MODEL_UNSUPPORTED": "ไม่พบหรือไม่รองรับโมเดลที่เลือก",
        "PROVIDER_TOOL_CALLING_UNSUPPORTED": "โมเดลที่เลือกไม่รองรับ Tool Calling",
        "PROVIDER_TIMEOUT": "ผู้ให้บริการใช้เวลาตอบนานเกินกำหนด กรุณาลองใหม่ด้วยตนเอง",
        "TOOL_CALL_LIMIT_EXCEEDED": "โมเดลขออ่านแหล่งข้อมูลเกิน 3 ครั้ง งานจึงถูกหยุด",
        "PROVIDER_ROUND_LIMIT_EXCEEDED": "การสร้างสคริปต์เกิน 3 รอบ งานจึงถูกหยุด",
        "RESEARCH_RESPONSE_INVALID": "ผลลัพธ์ Research ไม่สมบูรณ์ จึงยังไม่เปลี่ยน Script Editor",
    }
    assert RESEARCH_ERROR_CODES == frozenset(expected_messages)
    assert RESEARCH_PUBLIC_MESSAGES == expected_messages
