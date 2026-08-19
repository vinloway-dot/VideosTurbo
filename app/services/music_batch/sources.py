from __future__ import annotations

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
    plans = [
        SourcePlan(
            provider=provider,
            keywords=list(keywords),
            requested_duration=per_source,
        )
        for provider in normalized_sources
    ]
    return plans


T = TypeVar("T")


class UsedClipRegistry:
    def __init__(self) -> None:
        self._used: dict[str, set[str]] = {}

    @staticmethod
    def _provider(provider: str) -> str:
        return provider.strip().lower()

    @staticmethod
    def _clip_id(clip_id: object) -> str:
        return str(clip_id)

    def seen(self, provider: str, clip_id: object) -> bool:
        return self._clip_id(clip_id) in self._used.get(self._provider(provider), set())

    def mark(self, provider: str, clip_id: object) -> None:
        self._used.setdefault(self._provider(provider), set()).add(self._clip_id(clip_id))

    def filter_candidates(
        self,
        provider: str,
        candidates: Sequence[tuple[object, T]],
        *,
        avoid_reuse: bool,
    ) -> list[tuple[object, T]]:
        items = list(candidates)
        if not avoid_reuse or not items:
            return items

        unseen = [item for item in items if not self.seen(provider, item[0])]
        # Best effort: if every result has been seen, keep the provider results rather
        # than failing the whole batch solely because the search pool is exhausted.
        return unseen or items
