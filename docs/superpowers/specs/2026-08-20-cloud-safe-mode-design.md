# Cloud Safe Mode Design

## Goal
Make Music Batch safe to leave unattended on an Ubuntu GPU cloud instance: GPU encoder failures must not silently fall back to CPU, stopping/restarting the service must terminate FFmpeg children, interrupted batches must be recoverable automatically, and unsafe resource conditions must stop admission of new work.

## Scope
This is additive and must not change normal MoneyPrinterTurbo behavior outside Music Batch. Existing Video Generator fallback behavior remains unchanged. Cloud Safe Mode is opt-in through `MPT_CLOUD_SAFE_MODE=1` and the provided systemd units enable it by default.

## Architecture
1. **Fail-closed NVENC inside Music Batch.** The existing context-aware GPU hooks are the boundary. When a Music Batch render is inside `nvenc_gpu_context`, any selected NVENC encoder must either run successfully or raise. It must never invoke the core libx264 fallback and must not globally disable NVENC for unrelated jobs.
2. **systemd process ownership.** Ubuntu deployment runs Streamlit and optional recovery under systemd with `KillMode=control-group`, `Restart=on-failure`, bounded stop timeout, and final SIGKILL. This makes FFmpeg descendants part of the service cgroup so a stop/restart cannot leave orphan encoders running.
3. **Persistent recovery worker.** A recovery helper scans an explicitly configured Music Batch output root for non-terminal `batch_state.json` files and resumes them sequentially using the GPU-aware manager. A oneshot systemd recovery service can run after boot/network/GPU setup.
4. **Resource admission guard.** When Cloud Safe Mode is enabled, each new song checks disk, memory, normalized load average, NVIDIA temperature, and VRAM pressure. Low disk is fail-closed. Temporary pressure pauses admission until healthy or a timeout is reached. Existing in-flight FFmpeg jobs are not force-killed merely because utilization is high.

## Defaults
- `MPT_CLOUD_SAFE_MODE=0` outside the cloud unit files.
- Minimum free disk: 10 GiB.
- Maximum memory used: 90%.
- Maximum normalized 1-minute load: 1.25 per CPU.
- Maximum GPU temperature: 85 C.
- Maximum GPU memory used: 95%.
- Guard poll interval: 10 seconds.
- Guard wait timeout: 600 seconds.

## Recovery semantics
Terminal batches (`completed`, `completed_with_failures`, `failed`) are ignored. Non-terminal batches are loaded, `processing`/`retrying` songs are recovered to `pending`, then normal resume logic runs. Recovery processes batches sequentially to avoid an uncontrolled burst after reboot.

## Failure semantics
- NVENC failure in Music Batch: current song/batch follows existing fatal GPU path; no CPU fallback.
- Insufficient disk: fail before starting another song.
- Temporary RAM/load/GPU pressure: pause admission; if still unsafe after timeout, fail with a resource guard error.
- systemd stop/restart: terminate the whole cgroup, including FFmpeg descendants.

## Non-goals
This version does not preempt a currently encoding FFmpeg job solely because CPU usage is high, does not migrate an in-flight song from a failed GPU to another GPU, and does not alter the normal Video Generator's hardware-to-CPU fallback policy.
