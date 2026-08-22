from __future__ import annotations

from typing import Mapping, Protocol

from app.models.cloud_agent import ServiceSessionStatus, SessionCheckResult
from app.services.cloud_agent.errors import HumanRequiredError


class SessionProvider(Protocol):
    def check_session(
        self,
        *,
        headed: bool = False,
        job_id: str = "",
    ) -> SessionCheckResult: ...

    def repair_session(
        self,
        *,
        headed: bool = False,
        job_id: str = "",
    ) -> SessionCheckResult: ...


class SessionManager:
    """Apply bounded Check -> safe Repair -> Verify policy across services."""

    def __init__(self, providers: Mapping[str, SessionProvider]) -> None:
        self.providers = dict(providers)

    def check_all(self) -> dict[str, SessionCheckResult]:
        return {
            service: provider.check_session(headed=False)
            for service, provider in self.providers.items()
        }

    def ensure_service_ready(
        self,
        service: str,
        job_id: str,
    ) -> SessionCheckResult:
        provider = self._provider(service)
        initial = provider.check_session(headed=False, job_id=job_id)
        if initial.status is ServiceSessionStatus.READY:
            return initial

        if initial.status is ServiceSessionStatus.SESSION_EXPIRED:
            repair = provider.repair_session(headed=False, job_id=job_id)
            if repair.status in {
                ServiceSessionStatus.AUTO_RELOGIN,
                ServiceSessionStatus.READY,
            }:
                verified = provider.check_session(headed=False, job_id=job_id)
                if verified.status is ServiceSessionStatus.READY:
                    return verified
                self._raise_unready(service, job_id, verified)
            self._raise_unready(service, job_id, repair)

        self._raise_unready(service, job_id, initial)

    def ensure_all_ready(self, job_id: str) -> dict[str, SessionCheckResult]:
        return {
            service: self.ensure_service_ready(service, job_id)
            for service in self.providers
        }

    def _provider(self, service: str) -> SessionProvider:
        try:
            return self.providers[service]
        except KeyError as exc:
            raise ValueError(f"unsupported session service: {service}") from exc

    @staticmethod
    def _raise_unready(
        service: str,
        job_id: str,
        result: SessionCheckResult,
    ) -> None:
        if result.status is ServiceSessionStatus.ERROR:
            detail = result.message or "unknown session error"
            raise RuntimeError(f"{service} session error for job {job_id}: {detail}")

        raise HumanRequiredError(
            f"{service} session requires human recovery for job {job_id}: "
            f"{result.status.value}"
        )
