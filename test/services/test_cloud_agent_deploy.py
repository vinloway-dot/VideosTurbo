from pathlib import Path


WORKER_UNIT = Path("deploy/systemd/videosturbo-worker.service.example")
NGINX = Path("deploy/nginx/videosturbo.conf.example")


def test_cloud_agent_worker_unit_limits_restart_storms_to_ten_attempts():
    source = WORKER_UNIT.read_text(encoding="utf-8")

    assert "StartLimitIntervalSec=300" in source
    assert "StartLimitBurst=10" in source
    assert "Restart=on-failure" in source


def test_nginx_exposes_only_exact_authenticated_sse_route():
    source = NGINX.read_text(encoding="utf-8")
    assert "location = /api/v1/cloud-agent/events/stream" in source
    assert 'auth_basic "VideosTurbo control panel";' in source
    assert "proxy_pass http://127.0.0.1:8080;" in source
    assert "proxy_buffering off;" in source
    assert "proxy_cache off;" in source
    assert 'proxy_set_header Connection "";' in source
    assert "location /api/" not in source
    assert "/cloud-agent/internal/events" not in source
