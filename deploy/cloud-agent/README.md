# Cloud Agent deployment

Install Ubuntu packages for systemd, Xvfb/Openbox, x11vnc, noVNC/websockify, Google Chrome Stable and its sandbox dependencies. Run `uv sync --frozen` in `/opt/VideosTurbo`; install Playwright's supported browser dependencies only if Chrome is unavailable.

Repository unit templates deliberately use the `.service.example` suffix. For installation and `systemd-analyze verify`, copy each byte-for-byte to a valid `.service` filename outside the repository (or in `/etc/systemd/system/`). Then put non-secret runtime settings in `/etc/videosturbo/cloud-agent.env` (owned by `root:linuxuser`, mode `0640`). Keep API keys and configuration in server-side config/env files; never place them in units.

Create `/opt/VideosTurbo/storage`, browser-profile and browser-lock directories owned by `linuxuser:linuxuser`; the SQLite database must be writable by that user. Browser profiles are private data and must never be served by Nginx or application static routes.

Preserve the existing `videosturbo-xvfb`, `videosturbo-openbox`, `videosturbo-x11vnc` and `videosturbo-novnc` services. They use `DISPLAY=:99`; VNC/noVNC must remain loopback-only and be protected through the existing reverse proxy/authentication. Do not expose ports 5900 or 6080 publicly.

Run `systemctl daemon-reload`, enable the API/WebUI/worker units, then use the protected headed browser for the first Google Flow and Canva login. Check both sessions through the Cloud Agent API/UI before queueing work. Do not automate passwords, CAPTCHA, 2FA, OAuth consent, or device confirmation.

For the worker-driven progress stream, copy the exact SSE `location` block from
`deploy/nginx/videosturbo.conf.example` into the authenticated live server.
Run `sudo nginx -t` before reloading Nginx. Only
`/api/v1/cloud-agent/events/stream` is proxied to the loopback API; do not
proxy the internal event POST or a general `/api/` location.
