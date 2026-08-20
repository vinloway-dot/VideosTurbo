from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Sequence, TypeVar


@dataclass(frozen=True)
class SourcePlan:
    provider: str
    keywords: list[str]
    requested_duration: float


def build_source_plan(
    sources: list[str], keywords: list[str], target_duration: float
) -> list[SourcePlan]:
    normalized_sources = [source.strip().lower() for source in sources if source.strip()]
    if not normalized_sources:
        raise ValueError("at least one stock source is required")
    if target_duration < 0:
        raise ValueError("target_duration must be non-negative")

    per_source = target_duration / len(normalized_sources)
    return [
        SourcePlan(
            provider=provider,
            keywords=list(keywords),
            requested_duration=per_source,
        )
        for provider in normalized_sources
    ]


T = TypeVar("T")


class UsedClipRegistry:
    def __init__(self) -> None:
        self._used: dict[str, set[str]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _provider(provider: str) -> str:
        return provider.strip().lower()

    @staticmethod
    def _clip_id(clip_id: object) -> str:
        return str(clip_id)

    def seen(self, provider: str, clip_id: object) -> bool:
        with self._lock:
            return self._clip_id(clip_id) in self._used.get(
                self._provider(provider), set()
            )

    def mark(self, provider: str, clip_id: object) -> None:
        with self._lock:
            self._used.setdefault(self._provider(provider), set()).add(
                self._clip_id(clip_id)
            )

    def snapshot(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                provider: sorted(clip_ids)
                for provider, clip_ids in self._used.items()
            }

    def load_snapshot(self, snapshot: dict[str, list[str]] | None) -> None:
        with self._lock:
            self._used = {
                self._provider(provider): {self._clip_id(clip_id) for clip_id in clip_ids}
                for provider, clip_ids in (snapshot or {}).items()
            }

    def reserve_candidates(
        self,
        provider: str,
        candidates: Sequence[tuple[object, T]],
        *,
        avoid_reuse: bool,
    ) -> list[tuple[object, T]]:
        """Select and reserve unseen clips atomically for parallel batch workers.

        When duplicate avoidance is enabled, selecting candidates and recording their
        IDs must happen under one lock. Otherwise two workers can both observe the
        same clip as unseen before either worker records it. If the provider has no
        unseen candidates, preserve the approved best-effort behavior and return the
        original candidates instead of failing the batch solely because the pool was
        exhausted.
        """

        items = list(candidates)
        if not avoid_reuse or not items:
            return items

        normalized_provider = self._provider(provider)
        with self._lock:
            used = self._used.setdefault(normalized_provider, set())
            unseen = [item for item in items if self._clip_id(item[0]) not in used]
            if not unseen:
                return items
            used.update(self._clip_id(item[0]) for item in unseen)
            return unseen

    def filter_candidates(
        self,
        provider: str,
        candidates: Sequence[tuple[object, T]],
        *,
        avoid_reuse: bool,
    ) -> list[tuple[object, T]]:
        return self.reserve_candidates(
            provider,
            candidates,
            avoid_reuse=avoid_reuse,
        )
