from __future__ import annotations

import multiprocessing

import pytest

from app.services.cloud_agent.browser_lock import ProfileLock


def _hold_profile_lock(
    lock_dir: str,
    service: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    lock = ProfileLock(lock_dir)
    with lock.acquire(service, timeout_seconds=2.0):
        ready.set()
        if not release.wait(timeout=5.0):
            raise RuntimeError("timed out waiting to release profile lock")


def _start_holder(tmp_path, service: str):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_profile_lock,
        args=(str(tmp_path), service, ready, release),
    )
    process.start()
    assert ready.wait(timeout=5.0), "holder process did not acquire profile lock"
    return process, release


def _stop_holder(process, release) -> None:
    release.set()
    process.join(timeout=5.0)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5.0)
    assert process.exitcode == 0


def test_same_service_is_excluded_across_processes(tmp_path):
    process, release = _start_holder(tmp_path, "google_flow")
    try:
        contender = ProfileLock(tmp_path)
        with pytest.raises(TimeoutError, match="google_flow"):
            with contender.acquire("google_flow", timeout_seconds=0.2):
                pass
    finally:
        _stop_holder(process, release)


def test_different_services_use_independent_profile_locks(tmp_path):
    process, release = _start_holder(tmp_path, "google_flow")
    try:
        contender = ProfileLock(tmp_path)
        with contender.acquire("canva", timeout_seconds=0.5):
            pass
    finally:
        _stop_holder(process, release)
