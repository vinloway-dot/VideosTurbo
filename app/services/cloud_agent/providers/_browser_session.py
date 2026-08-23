from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.models.cloud_agent import ServiceSessionStatus, SessionCheckResult


class BrowserSessionProvider:
    """Open one real service in its persistent profile and classify observable state."""

    def __init__(
        self,
        browser: Any,
        *,
        service: str,
        service_url: str,
        classifier: Callable[..., ServiceSessionStatus],
    ) -> None:
        self.browser = browser
        self.service = service
        self.service_url = str(service_url or "").strip()
        self.classifier = classifier

    def check_session(
        self,
        *,
        headed: bool = False,
        job_id: str = "",
    ) -> SessionCheckResult:
        if not self.service_url:
            return self._result(
                ServiceSessionStatus.ERROR,
                message=f"{self.service} URL is not configured",
            )

        try:
            with self.browser.open(self.service, headed=headed) as context:
                page = self._page(context)
                page.goto(self.service_url, wait_until="domcontentloaded")
                self._wait_for_observable_state(page)
                status = self.classifier(url=page.url, html=page.content())
                evidence_path = self._capture(page, job_id, label="session-check")
                return self._result(status, evidence_path=evidence_path)
        except TimeoutError:
            return self._result(
                ServiceSessionStatus.ERROR,
                message=f"{self.service} browser profile is busy",
            )
        except Exception as exc:
            return self._result(
                ServiceSessionStatus.ERROR,
                message=f"{self.service} session check failed: {type(exc).__name__}",
            )

    def repair_session(
        self,
        *,
        headed: bool = False,
        job_id: str = "",
    ) -> SessionCheckResult:
        if not self.service_url:
            return self._result(
                ServiceSessionStatus.ERROR,
                message=f"{self.service} URL is not configured",
            )

        try:
            with self.browser.open(self.service, headed=headed) as context:
                page = self._page(context)
                page.goto(self.service_url, wait_until="domcontentloaded")
                self._wait_for_observable_state(page)
                status = self.classifier(url=page.url, html=page.content())
                if status is not ServiceSessionStatus.SESSION_EXPIRED:
                    evidence_path = self._capture(page, job_id, label="session-repair")
                    return self._result(status, evidence_path=evidence_path)

                continue_google = page.get_by_role(
                    "button",
                    name=re.compile(r"continue\s+with\s+google", re.IGNORECASE),
                )
                if not continue_google.is_visible():
                    evidence_path = self._capture(page, job_id, label="session-repair")
                    return self._result(status, evidence_path=evidence_path)

                continue_google.click()
                evidence_path = self._capture(page, job_id, label="session-repair")
                return self._result(
                    ServiceSessionStatus.AUTO_RELOGIN,
                    evidence_path=evidence_path,
                )
        except TimeoutError:
            return self._result(
                ServiceSessionStatus.ERROR,
                message=f"{self.service} browser profile is busy",
            )
        except Exception as exc:
            return self._result(
                ServiceSessionStatus.ERROR,
                message=f"{self.service} session repair failed: {type(exc).__name__}",
            )

    @staticmethod
    def _page(context: Any) -> Any:
        pages = list(getattr(context, "pages", []) or [])
        if pages:
            return pages[0]
        return context.new_page()

    def _capture(self, page: Any, job_id: str, *, label: str) -> str:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return ""
        screenshot_path, _ = self.browser.capture_evidence(
            normalized_job_id,
            self.service,
            page,
            label=label,
        )
        return str(screenshot_path)

    def _wait_for_observable_state(self, page: Any) -> None:
        """Allow a service provider to await its proven ready-state selector."""

    def _result(
        self,
        status: ServiceSessionStatus,
        *,
        message: str = "",
        evidence_path: str = "",
    ) -> SessionCheckResult:
        return SessionCheckResult(
            service=self.service,
            status=status,
            message=message,
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            evidence_path=evidence_path,
        )
