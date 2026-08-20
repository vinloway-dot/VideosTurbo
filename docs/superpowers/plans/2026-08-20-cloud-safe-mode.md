# Cloud Safe Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make unattended Ubuntu GPU Music Batch jobs fail closed on NVENC errors, recover after restart, and stop all FFmpeg descendants when the service is stopped.

**Architecture:** Keep the normal Video Generator unchanged. Extend only the Music Batch GPU hook boundary for strict NVENC, add an opt-in resource admission guard and persistent recovery helper, then provide systemd units that own the full process cgroup.

**Tech Stack:** Python 3.11+, Streamlit, FFmpeg/NVENC, `nvidia-smi`, systemd, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-20-cloud-safe-mode-design.md`

## Global Constraints

- Existing Video Generator behavior must remain unchanged.
- Music Batch NVENC must never silently fall back to libx264.
- One GPU remains supported; two or more GPUs continue round-robin scheduling.
- Cloud Safe Mode is opt-in outside the supplied systemd service.
- No new third-party Python dependency.

---

### Task 1: Fail-closed NVENC in Music Batch

**Files:**
- Modify: `app/services/music_batch/gpu.py`
- Test: `test/services/music_batch/test_gpu_scheduling.py`

**Interfaces:**
- Produces: `nvenc_gpu_context(gpu_index)` marks the current Music Batch render as strict NVENC.
- Produces: GPU-aware MoviePy and concat hooks raise on NVENC failure instead of invoking libx264 fallback.

- [ ] Add tests proving an NVENC MoviePy failure raises and never calls `_fallback_write_videofile`.
- [ ] Add tests proving direct FFmpeg concat failure raises and never runs libx264 while inside Music Batch context.
- [ ] Implement context-aware fail-closed behavior.
- [ ] Run Music Batch GPU tests and Ruff.

### Task 2: Resource admission guard

**Files:**
- Create: `app/services/music_batch/resource_guard.py`
- Modify: `app/services/music_batch/gpu_manager.py`
- Test: `test/services/music_batch/test_resource_guard.py`

**Interfaces:**
- Produces: `ResourceGuard.from_env()` and `wait_until_safe(output_root, gpu_index)`.
- Consumes: `MPT_CLOUD_SAFE_MODE` and threshold environment variables.

- [ ] Write tests for disabled mode, low-disk fail-closed, temporary pressure wait/recovery, and timeout.
- [ ] Implement Linux-friendly stdlib metrics plus optional `nvidia-smi` metrics.
- [ ] Call guard before each song begins rendering.
- [ ] Run targeted tests and Ruff.

### Task 3: Persistent recovery helper

**Files:**
- Create: `app/services/music_batch/recovery.py`
- Create: `scripts/music_batch_recover.py`
- Test: `test/services/music_batch/test_recovery.py`

**Interfaces:**
- Produces: `find_incomplete_batch_dirs(output_root)`.
- Produces: `resume_incomplete_batches(output_root, manager_factory=...)`.

- [ ] Test terminal batches are ignored and interrupted/processing batches are discovered newest-first.
- [ ] Test recovery calls GPU-aware `resume_batch` sequentially and continues after a failed batch.
- [ ] Implement helper and CLI.
- [ ] Run targeted tests and Ruff.

### Task 4: systemd cloud process ownership

**Files:**
- Create: `deploy/systemd/videosturbo-webui.service.example`
- Create: `deploy/systemd/videosturbo-music-batch-recovery.service.example`
- Create: `docs/cloud-safe-mode.md`
- Test: `test/services/music_batch/test_cloud_deploy.py`

**Interfaces:**
- WebUI unit uses `KillMode=control-group`, `Restart=on-failure`, bounded stop timeout, Cloud Safe Mode env.
- Recovery unit runs the recovery CLI against a configured output root.

- [ ] Write tests checking the safety-critical unit directives.
- [ ] Add systemd unit examples and deployment documentation.
- [ ] Run targeted tests and Ruff.

### Task 5: Verification

- [ ] Run `uv run ruff check app/services/music_batch test/services/music_batch scripts`.
- [ ] Run `uv run pytest test/services/music_batch -q`.
- [ ] Run full CI on Python 3.11, Python 3.13, and Windows smoke tests.
- [ ] Do not merge until CI is green and a local Windows `Parallel Jobs=1` NVENC smoke test still passes.
