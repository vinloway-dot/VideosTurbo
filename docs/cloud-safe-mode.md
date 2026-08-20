# Cloud Safe Mode (Ubuntu GPU)

Cloud Safe Mode is intended for unattended Music Batch runs on hourly GPU servers. It is additive: the normal Video Generator keeps its existing encoder fallback behavior, while Music Batch fails closed when NVENC fails.

## What it protects

- **No silent CPU fallback in Music Batch:** if an NVENC render fails, that render fails instead of silently switching to `libx264` and driving CPU usage to 100%.
- **Whole process-tree shutdown with systemd:** the supplied units use `KillMode=control-group`. Stopping or restarting the service terminates Streamlit/Python and FFmpeg descendants in the service cgroup.
- **Persistent batch recovery:** `scripts/music_batch_recover.py` scans an output root for non-terminal `batch_state.json` files and resumes them sequentially with the GPU-aware manager.
- **No recovery/WebUI overlap:** both systemd units hold the same non-blocking `flock` lock. Recovery runs before the WebUI at boot, and a manual recovery attempt cannot accidentally render the same batch while the WebUI service is active.
- **Localhost-only WebUI by default:** the example service binds Streamlit to `127.0.0.1`, so a newly created public cloud server does not expose the control UI directly to the Internet.
- **Resource admission guard:** before a new song starts, Cloud Safe Mode checks free disk, memory pressure, normalized 1-minute load, GPU temperature, and VRAM pressure. Low disk fails closed. Temporary pressure pauses admission until healthy or until the configured timeout.

## Recommended Ubuntu layout

```text
/opt/VideosTurbo/                 application checkout
/opt/VideosTurbo/.venv/           uv environment
/var/lib/videosturbo/output/       Music Batch output root
/etc/systemd/system/               installed unit files
```

Create a dedicated service account and output directory, then adjust ownership to your deployment policy:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin videosturbo || true
sudo mkdir -p /var/lib/videosturbo/output
sudo chown -R videosturbo:videosturbo /var/lib/videosturbo /opt/VideosTurbo
```

Install the example units after checking the paths:

```bash
sudo cp deploy/systemd/videosturbo-webui.service.example /etc/systemd/system/videosturbo-webui.service
sudo cp deploy/systemd/videosturbo-music-batch-recovery.service.example /etc/systemd/system/videosturbo-music-batch-recovery.service
sudo systemctl daemon-reload
sudo systemctl enable videosturbo-music-batch-recovery.service
sudo systemctl enable --now videosturbo-webui.service
```

When both units are enabled, recovery is ordered before the WebUI on future boots. The shared lock `/run/lock/videosturbo-music-batch.lock` prevents the two services from processing Music Batch work at the same time.

To run recovery immediately while the WebUI is already active, stop the WebUI first, run recovery, then start it again:

```bash
sudo systemctl stop videosturbo-webui.service
sudo systemctl start videosturbo-music-batch-recovery.service
sudo systemctl start videosturbo-webui.service
```

To stop the WebUI safely:

```bash
sudo systemctl stop videosturbo-webui.service
```

Because the unit uses `KillMode=control-group`, FFmpeg processes created by the service are terminated with the service. After `TimeoutStopSec`, systemd sends the final kill signal if descendants did not exit cleanly.

## Access the WebUI safely

The supplied WebUI service listens only on `127.0.0.1:8501`. From your local computer, create an SSH tunnel:

```bash
ssh -L 8501:127.0.0.1:8501 videosturbo@SERVER_IP
```

Then open `http://127.0.0.1:8501` on your local computer. If you later place VideosTurbo behind an authenticated reverse proxy, you may deliberately change the bind address, but do not expose an unauthenticated Streamlit control panel directly to the public Internet.

## Environment variables

The example WebUI unit enables:

```text
MPT_CLOUD_SAFE_MODE=1
MPT_CLOUD_MIN_FREE_DISK_GB=10
MPT_CLOUD_MAX_MEMORY_PERCENT=90
MPT_CLOUD_MAX_LOAD_RATIO=1.25
MPT_CLOUD_MAX_GPU_TEMP_C=85
MPT_CLOUD_MAX_GPU_MEMORY_PERCENT=95
MPT_CLOUD_GUARD_POLL_SECONDS=10
MPT_CLOUD_GUARD_TIMEOUT_SECONDS=600
```

The recovery unit additionally sets:

```text
MPT_MUSIC_BATCH_OUTPUT_ROOT=/var/lib/videosturbo/output
```

Change that path if the Music Batch UI writes somewhere else.

## Operational checks

After deployment, verify NVIDIA and NVENC before starting a paid batch:

```bash
nvidia-smi
ffmpeg -hide_banner -encoders | grep nvenc
systemctl status videosturbo-webui.service
```

A practical shutdown test is to start a short Music Batch, confirm an FFmpeg child exists, run `sudo systemctl stop videosturbo-webui.service`, then confirm the service cgroup and FFmpeg descendants are gone before treating the cloud image as production-ready.

## Limits of this version

Cloud Safe Mode controls **admission of new songs**; it does not terminate a healthy in-flight FFmpeg process simply because CPU utilization is high. It also does not migrate an already-running song from one failed GPU to another GPU. Those behaviors require stronger per-job process isolation and are intentionally outside this first safety layer.
