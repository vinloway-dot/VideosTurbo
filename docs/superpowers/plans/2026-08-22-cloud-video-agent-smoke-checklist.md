# Cloud Video Agent Ubuntu Smoke Checklist

## Checkpoint

- Date: 2026-08-23 UTC
- VPS runtime: Ubuntu, `linuxuser:linuxuser`, `/opt/VideosTurbo`
- Verified source commits: `61c4fe1`, `b2ee613`
- Scope: Task 14 non-paid deployment and preflight checkpoint
- Paid TTS/Google Flow operations performed: none
- Gate state: `READY_FOR_PAID_E2E`

No cookies, credentials, browser-profile contents, signed URLs, private media, or
secret-bearing screenshots are included in this record.

## Automated verification

- `uv lock --check`: PASS
- `uv sync --frozen`: PASS
- `python -m compileall app webui`: PASS
- `ruff check app webui test`: PASS
- Full pytest: PASS (`927 passed`, `11 skipped`)
- CI-equivalent coverage run: PASS (`73%`, required minimum `70%`)
- Temporary `.service` copies: API PASS, Worker PASS, WebUI PASS
- Installed live units: `systemd-analyze verify` PASS

## Live non-paid preflight

- API: active, loopback `127.0.0.1:8080`, health PASS
- WebUI: active, loopback `127.0.0.1:8501`, HTTP 200
- Worker: active, heartbeat online and refreshed after an independent restart
- Runtime independence: restarting each application service left the other two
  running; API and WebUI remained reachable
- Remote browser: Xvfb, Openbox, x11vnc, and noVNC active
- VNC/noVNC: loopback-only on `127.0.0.1:5900/6080`
- Google Flow session: READY on the approved production project
- Canva session: READY on the approved production design
- Flow `/th/` locale: compatible with observable project-shell state
- Profile locks: no live owner after checks; no Chrome Singleton locks remained
- Storage/profile/lock ownership and writability: PASS for `linuxuser:linuxuser`
- SQLite: read/write open PASS; `PRAGMA quick_check` returned `ok`
- Live config: server-side, `linuxuser:linuxuser`, mode `0600`, service-readable
- ffprobe: PASS (Ubuntu FFmpeg 6.1.1 toolchain)
- TTS: existing adapter and Edge/Azure-v1 dependency/timeout configuration loaded;
  no synthesis request was made
- Recent API/WebUI/Worker error-level journal entries: none

## Session recovery evidence

- Protected headed Google Flow recovery through Xvfb/noVNC: PASS
- Post-recovery Google Flow check: READY
- Canva check using its existing persistent profile: READY
- Safe Auto Re-login and controlled HUMAN_REQUIRED policy: automated regression PASS
- Password, CAPTCHA, 2FA, device confirmation, and OAuth automation: not used

## Remaining paid/live Gate F checks

These checks remain pending explicit `PAID_E2E_AUTHORIZED` approval:

- Real approximately 63-second canonical TTS + six-clip Google Flow + Canva E2E
- Live over-policy narration rejection before Flow credit consumption
- Live restart recovery at `TTS_READY` and `FLOW_READY`
- Live final-validation failure retaining source clips
- Final job timing, playback, export, validation, cleanup, and COMPLETED evidence

The corresponding fake E2E, over-policy, restart-resume, source-retention, cleanup,
and checkpoint regressions passed in the automated suite. No Task 15 work started.

## Expected paid operation budget

- Canonical TTS calls: 1
- Google Flow generation requests: 1
- Expected source clips: 6
- Canva final assembly/export: 1
