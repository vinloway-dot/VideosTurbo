from typing import TYPE_CHECKING, Final


if TYPE_CHECKING:
    from app.services.cloud_agent.research.models import ResearchUsageAccounting


RESEARCH_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "URL_REQUIRED",
        "URL_INVALID",
        "URL_TARGET_NOT_PUBLIC",
        "URL_REDIRECT_REJECTED",
        "URL_FETCH_FAILED",
        "URL_CONTENT_UNSUPPORTED",
        "URL_CONTENT_TOO_LARGE",
        "PDF_INVALID",
        "PDF_TOO_LARGE",
        "PDF_TEXT_UNAVAILABLE",
        "SOURCE_EVIDENCE_EMPTY",
        "SOURCE_CONTEXT_TOO_LARGE",
        "PROVIDER_API_KEY_MISSING",
        "PROVIDER_AUTHENTICATION_FAILED",
        "PROVIDER_MODEL_UNSUPPORTED",
        "PROVIDER_TOOL_CALLING_UNSUPPORTED",
        "PROVIDER_TIMEOUT",
        "TOOL_CALL_LIMIT_EXCEEDED",
        "PROVIDER_ROUND_LIMIT_EXCEEDED",
        "RESEARCH_RESPONSE_INVALID",
    }
)

RESEARCH_PUBLIC_MESSAGES: Final[dict[str, str]] = {
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
_DEFAULT_PUBLIC_MESSAGE = RESEARCH_PUBLIC_MESSAGES["RESEARCH_RESPONSE_INVALID"]


def public_research_message(code: str) -> str:
    return RESEARCH_PUBLIC_MESSAGES.get(str(code or "").strip(), _DEFAULT_PUBLIC_MESSAGE)


class ResearchError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        accounting: "ResearchUsageAccounting | None" = None,
        retryable: bool = False,
    ) -> None:
        normalized_code = str(code or "").strip()
        if normalized_code not in RESEARCH_ERROR_CODES:
            normalized_code = "RESEARCH_RESPONSE_INVALID"
        self.code = normalized_code
        self.detail = str(detail or "").strip()
        self.accounting = accounting
        self.retryable = bool(retryable)
        super().__init__(public_research_message(self.code))
