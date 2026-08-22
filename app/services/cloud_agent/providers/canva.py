from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers._browser_session import BrowserSessionProvider
from app.services.cloud_agent.providers._session_detection import (
    classify_security_challenge,
)


def classify_canva_session(*, url: str, html: str) -> ServiceSessionStatus:
    """Classify observable Canva editor state without relying on cookies."""
    challenge = classify_security_challenge(html=html)
    if challenge is not None:
        return challenge

    page_url = str(url or "").lower()
    body = str(html or "").lower()

    if (
        "/login" in page_url
        or "/signup" in page_url
        or "log in or sign up" in body
        or "log in to canva" in body
        or "continue with google" in body
    ):
        return ServiceSessionStatus.SESSION_EXPIRED

    is_design_editor = "canva.com/design/" in page_url and "/edit" in page_url
    has_share_control = 'aria-label="share"' in body or ">share<" in body
    has_editor_surface = "canva editor" in body
    if is_design_editor and has_share_control and has_editor_surface:
        return ServiceSessionStatus.READY

    return ServiceSessionStatus.ERROR


class CanvaSessionProvider(BrowserSessionProvider):
    def __init__(self, browser, *, service_url: str) -> None:
        super().__init__(
            browser,
            service="canva",
            service_url=service_url,
            classifier=classify_canva_session,
        )
