from pathlib import Path

import pytest

from app.services.music_batch.resource_guard import (
    GiB,
    GuardThresholds,
    ResourceGuard,
    ResourceGuardError,
    ResourceSnapshot,
)


def _snapshot(**overrides):
    values = {
        "disk_free_bytes": 50 * GiB,
        "memory_used_ratio": 0.25,
        "load_ratio": 0.20,
        "gpu_temp_c": 50.0,
        "gpu_memory_used_ratio": 0.30,
    }
    values.update(overrides)
    return ResourceSnapshot(**values)


def test_disabled_guard_does_not_collect_metrics(tmp_path):
    calls = []
    guard = ResourceGuard(
        enabled=False,
        snapshot_provider=lambda *_args: calls.append(True),
    )

    guard.wait_until_safe(tmp_path, gpu_index=0)

    assert calls == []


def test_low_disk_fails_closed_immediately(tmp_path):
    guard = ResourceGuard(
        enabled=True,
        snapshot_provider=lambda *_args: _snapshot(disk_free_bytes=5 * GiB),
        thresholds=GuardThresholds(min_free_disk_bytes=10 * GiB),
    )

    with pytest.raises(ResourceGuardError, match="free disk"):
        guard.wait_until_safe(tmp_path, gpu_index=0)


def test_temporary_pressure_waits_until_resources_recover(tmp_path):
    snapshots = iter(
        [
            _snapshot(memory_used_ratio=0.96),
            _snapshot(memory_used_ratio=0.40),
        ]
    )
    sleeps = []
    times = iter([0.0, 0.0, 1.0])
    guard = ResourceGuard(
        enabled=True,
        snapshot_provider=lambda *_args: next(snapshots),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: next(times),
        thresholds=GuardThresholds(poll_interval_seconds=1, wait_timeout_seconds=10),
    )

    guard.wait_until_safe(tmp_path, gpu_index=0)

    assert sleeps == [1]


def test_pressure_timeout_fails_instead_of_starting_another_song(tmp_path):
    times = iter([0.0, 0.0, 2.0])
    guard = ResourceGuard(
        enabled=True,
        snapshot_provider=lambda *_args: _snapshot(gpu_temp_c=90.0),
        sleep_fn=lambda _seconds: None,
        monotonic_fn=lambda: next(times),
        thresholds=GuardThresholds(
            max_gpu_temp_c=85.0,
            poll_interval_seconds=1,
            wait_timeout_seconds=1,
        ),
    )

    with pytest.raises(ResourceGuardError, match="GPU temperature"):
        guard.wait_until_safe(tmp_path, gpu_index=0)


def test_from_env_enables_cloud_safe_mode(monkeypatch):
    monkeypatch.setenv("MPT_CLOUD_SAFE_MODE", "1")
    monkeypatch.setenv("MPT_CLOUD_MIN_FREE_DISK_GB", "12")
    monkeypatch.setenv("MPT_CLOUD_MAX_MEMORY_PERCENT", "88")

    guard = ResourceGuard.from_env()

    assert guard.enabled is True
    assert guard.thresholds.min_free_disk_bytes == 12 * GiB
    assert guard.thresholds.max_memory_used_ratio == pytest.approx(0.88)
