from pathlib import Path


WEBUI_UNIT = Path("deploy/systemd/videosturbo-webui.service.example")
RECOVERY_UNIT = Path("deploy/systemd/videosturbo-music-batch-recovery.service.example")
LOCK_PATH = "/run/lock/videosturbo-music-batch.lock"


def test_webui_systemd_unit_owns_and_kills_child_processes():
    unit = WEBUI_UNIT.read_text(encoding="utf-8")

    assert "KillMode=control-group" in unit
    assert "Restart=on-failure" in unit
    assert "TimeoutStopSec=" in unit
    assert "MPT_CLOUD_SAFE_MODE=1" in unit


def test_webui_systemd_unit_is_localhost_only_by_default():
    unit = WEBUI_UNIT.read_text(encoding="utf-8")

    assert "MPT_WEBUI_HOST=127.0.0.1" in unit
    assert "--server.address=127.0.0.1" in unit
    assert "--server.address=0.0.0.0" not in unit


def test_recovery_systemd_unit_runs_persistent_batch_recovery():
    unit = RECOVERY_UNIT.read_text(encoding="utf-8")

    assert "Type=oneshot" in unit
    assert "scripts/music_batch_recover.py" in unit
    assert "KillMode=control-group" in unit
    assert "MPT_MUSIC_BATCH_OUTPUT_ROOT" in unit


def test_webui_and_recovery_share_nonblocking_process_lock():
    webui = WEBUI_UNIT.read_text(encoding="utf-8")
    recovery = RECOVERY_UNIT.read_text(encoding="utf-8")

    assert LOCK_PATH in webui
    assert LOCK_PATH in recovery
    assert "flock -n" in webui
    assert "flock -n" in recovery
    assert "Before=videosturbo-webui.service" in recovery
