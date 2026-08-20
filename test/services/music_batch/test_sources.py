import pytest

from app.services.music_batch.sources import (
    UsedClipRegistry,
    build_source_plan,
)


def test_build_source_plan_distributes_duration_across_selected_sources():
    plans = build_source_plan(
        ["pexels", "pixabay", "coverr"], ["ocean", "forest"], 180
    )
    assert [p.provider for p in plans] == ["pexels", "pixabay", "coverr"]
    assert sum(p.requested_duration for p in plans) == pytest.approx(180)


def test_build_source_plan_rejects_empty_sources():
    with pytest.raises(ValueError, match="at least one stock source"):
        build_source_plan([], ["ocean"], 60)


def test_used_clip_registry_filters_seen_but_can_fallback():
    registry = UsedClipRegistry()
    registry.mark("pexels", "123")
    candidates = [("123", "a"), ("456", "b")]
    assert registry.filter_candidates(
        "pexels", candidates, avoid_reuse=True
    ) == [("456", "b")]
    assert registry.filter_candidates(
        "pexels", [("123", "a")], avoid_reuse=True
    ) == [("123", "a")]


def test_used_clip_registry_reserves_unseen_candidates_atomically():
    registry = UsedClipRegistry()

    first = registry.reserve_candidates(
        "pexels",
        [("shared", "shared-a"), ("first-only", "first")],
        avoid_reuse=True,
    )
    second = registry.reserve_candidates(
        "pexels",
        [("shared", "shared-b"), ("second-only", "second")],
        avoid_reuse=True,
    )

    assert first == [("shared", "shared-a"), ("first-only", "first")]
    assert second == [("second-only", "second")]
    assert registry.seen("pexels", "shared") is True
    assert registry.seen("pexels", "second-only") is True


def test_used_clip_registry_reservation_keeps_best_effort_fallback():
    registry = UsedClipRegistry()
    registry.mark("pexels", "only")

    assert registry.reserve_candidates(
        "pexels", [("only", "clip")], avoid_reuse=True
    ) == [("only", "clip")]


def test_used_clip_registry_does_not_filter_when_option_is_off():
    registry = UsedClipRegistry()
    registry.mark("pixabay", "x")
    candidates = [("x", "clip")]
    assert registry.filter_candidates(
        "pixabay", candidates, avoid_reuse=False
    ) == candidates


def test_registry_is_scoped_by_provider():
    registry = UsedClipRegistry()
    registry.mark("pexels", "same-id")
    assert registry.seen("pexels", "same-id") is True
    assert registry.seen("coverr", "same-id") is False
