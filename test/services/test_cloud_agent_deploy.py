from pathlib import Path


WORKER_UNIT = Path("deploy/systemd/videosturbo-worker.service.example")


def test_cloud_agent_worker_unit_limits_restart_storms_to_ten_attempts():
    source = WORKER_UNIT.read_text(encoding="utf-8")

    assert "StartLimitIntervalSec=300" in source
    assert "StartLimitBurst=10" in source
    assert "Restart=on-failure" in source
