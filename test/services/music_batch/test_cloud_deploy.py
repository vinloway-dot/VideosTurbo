from pathlib import Path


def test_webui_systemd_unit_owns_and_kills_child_processes():
    unit = Path("deploy/systemd/videosturbo-webui.service.example").read_text(
        encoding="utf-8"
    )

    assert "KillMode=control-group" in unit
    assert "Restart=on-failure" in unit
    assert "TimeoutStopSec=" in unit
    assert "MPT_CLOUD_SAFE_MODE=1" in unit


def test_recovery_systemd_unit_runs_persistent_batch_recovery():
    unit = Path(
        "deploy/systemd/videosturbo-music-batch-recovery.service.example"
    ).read_text(encoding="utf-8")

    assert "Type=oneshot" in unit
    assert "scripts/music_batch_recover.py" in unit
    assert "KillMode=control-group" in unit
    assert "MPT_MUSIC_BATCH_OUTPUT_ROOT" in unit
