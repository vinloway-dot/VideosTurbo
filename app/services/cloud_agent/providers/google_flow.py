from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers._browser_session import BrowserSessionProvider
from app.services.cloud_agent.providers._session_detection import (
    classify_security_challenge,
)


def classify_google_flow_session(*, url: str, html: str) -> ServiceSessionStatus:
    """Classify observable Google Flow page state without relying on cookies."""
    challenge = classify_security_challenge(html=html)
    if challenge is not None:
        return challenge

    page_url = str(url or "").lower()
    body = str(html or "").lower()

    if (
        "accounts.google.com" in page_url
        or "sign in" in body
        or "continue with google" in body
    ):
        return ServiceSessionStatus.SESSION_EXPIRED

    has_agent_control = 'aria-label="agent"' in body or ">agent<" in body
    has_prompt_box = 'aria-label="prompt"' in body or "prompt box" in body
    if has_agent_control and has_prompt_box:
        return ServiceSessionStatus.READY

    return ServiceSessionStatus.ERROR


class GoogleFlowSessionProvider(BrowserSessionProvider):
    def __init__(self, browser, *, service_url: str) -> None:
        super().__init__(
            browser,
            service="google_flow",
            service_url=service_url,
            classifier=classify_google_flow_session,
        )
