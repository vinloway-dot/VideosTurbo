from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger

GiB = 1024**3


class ResourceGuardError(RuntimeError):
    """Raised when Cloud Safe Mode cannot safely admit another song."""


@dataclass(frozen=True)
class ResourceSnapshot:
    disk_free_bytes: int
    memory_used_ratio: float | None = None
    load_ratio: float | None = None
    gpu_temp_c: float | None = None
    gpu_memory_used_ratio: float | None = None


@dataclass(frozen=True)
class GuardThresholds:
    min_free_disk_bytes: int = 10 * GiB
    max_memory_used_ratio: float = 0.90
    max_load_ratio: float = 1.25
    max_gpu_temp_c: float = 85.0
    max_gpu_memory_used_ratio: float = 0.95
    poll_interval_seconds: float = 10.0
    wait_timeout_seconds: float = 600.0


SnapshotProvider = Callable[[Path, int | None], ResourceSnapshot]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_memory_used_ratio() -> float | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return None
    values: dict[str, int] = {}
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            number = raw.strip().split()[0]
            values[key] = int(number)
    except (OSError, ValueError, IndexError):
        return None
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return max(0.0, min(1.0, 1.0 - (available / total)))


def _read_load_ratio() -> float | None:
    try:
        load_1m = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        return None
    cpus = max(1, int(os.cpu_count() or 1))
    return max(0.0, load_1m / cpus)


def _read_gpu_metrics(gpu_index: int | None) -> tuple[float | None, float | None]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None, None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=index,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if result.returncode != 0:
        return None, None

    temps: list[float] = []
    memory_ratios: list[float] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            index = int(parts[0])
            temp = float(parts[1])
            memory_used = float(parts[2])
            memory_total = float(parts[3])
        except ValueError:
            continue
        if gpu_index is not None and index != gpu_index:
            continue
        temps.append(temp)
        if memory_total > 0:
            memory_ratios.append(memory_used / memory_total)

    return (
        max(temps) if temps else None,
        max(memory_ratios) if memory_ratios else None,
    )


def collect_resource_snapshot(output_root: Path, gpu_index: int | None) -> ResourceSnapshot:
    disk = shutil.disk_usage(Path(output_root))
    gpu_temp, gpu_memory_ratio = _read_gpu_metrics(gpu_index)
    return ResourceSnapshot(
        disk_free_bytes=int(disk.free),
        memory_used_ratio=_read_memory_used_ratio(),
        load_ratio=_read_load_ratio(),
        gpu_temp_c=gpu_temp,
        gpu_memory_used_ratio=gpu_memory_ratio,
    )


class ResourceGuard:
    def __init__(
        self,
        *,
        enabled: bool,
        thresholds: GuardThresholds | None = None,
        snapshot_provider: SnapshotProvider = collect_resource_snapshot,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = bool(enabled)
        self.thresholds = thresholds or GuardThresholds()
        self.snapshot_provider = snapshot_provider
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn

    @classmethod
    def from_env(cls) -> "ResourceGuard":
        def number(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None or not raw.strip():
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning(f"invalid {name}={raw!r}; using {default}")
                return default

        thresholds = GuardThresholds(
            min_free_disk_bytes=int(number("MPT_CLOUD_MIN_FREE_DISK_GB", 10) * GiB),
            max_memory_used_ratio=number("MPT_CLOUD_MAX_MEMORY_PERCENT", 90) / 100,
            max_load_ratio=number("MPT_CLOUD_MAX_LOAD_RATIO", 1.25),
            max_gpu_temp_c=number("MPT_CLOUD_MAX_GPU_TEMP_C", 85),
            max_gpu_memory_used_ratio=(
                number("MPT_CLOUD_MAX_GPU_MEMORY_PERCENT", 95) / 100
            ),
            poll_interval_seconds=number("MPT_CLOUD_GUARD_POLL_SECONDS", 10),
            wait_timeout_seconds=number("MPT_CLOUD_GUARD_TIMEOUT_SECONDS", 600),
        )
        return cls(
            enabled=_truthy(os.getenv("MPT_CLOUD_SAFE_MODE")),
            thresholds=thresholds,
        )

    def _temporary_pressure_reasons(self, snapshot: ResourceSnapshot) -> list[str]:
        reasons: list[str] = []
        t = self.thresholds
        if (
            snapshot.memory_used_ratio is not None
            and snapshot.memory_used_ratio > t.max_memory_used_ratio
        ):
            reasons.append(
                f"memory {snapshot.memory_used_ratio * 100:.1f}% > "
                f"{t.max_memory_used_ratio * 100:.1f}%"
            )
        if snapshot.load_ratio is not None and snapshot.load_ratio > t.max_load_ratio:
            reasons.append(
                f"normalized load {snapshot.load_ratio:.2f} > {t.max_load_ratio:.2f}"
            )
        if snapshot.gpu_temp_c is not None and snapshot.gpu_temp_c > t.max_gpu_temp_c:
            reasons.append(
                f"GPU temperature {snapshot.gpu_temp_c:.1f}C > {t.max_gpu_temp_c:.1f}C"
            )
        if (
            snapshot.gpu_memory_used_ratio is not None
            and snapshot.gpu_memory_used_ratio > t.max_gpu_memory_used_ratio
        ):
            reasons.append(
                f"GPU memory {snapshot.gpu_memory_used_ratio * 100:.1f}% > "
                f"{t.max_gpu_memory_used_ratio * 100:.1f}%"
            )
        return reasons

    def wait_until_safe(self, output_root: Path, gpu_index: int | None) -> None:
        if not self.enabled:
            return

        started = self.monotonic_fn()
        while True:
            snapshot = self.snapshot_provider(Path(output_root), gpu_index)
            if snapshot.disk_free_bytes < self.thresholds.min_free_disk_bytes:
                raise ResourceGuardError(
                    "Cloud Safe Mode blocked new work: free disk "
                    f"{snapshot.disk_free_bytes / GiB:.1f} GiB is below "
                    f"{self.thresholds.min_free_disk_bytes / GiB:.1f} GiB"
                )

            reasons = self._temporary_pressure_reasons(snapshot)
            if not reasons:
                return

            elapsed = self.monotonic_fn() - started
            if elapsed >= self.thresholds.wait_timeout_seconds:
                raise ResourceGuardError(
                    "Cloud Safe Mode resource wait timed out: " + "; ".join(reasons)
                )

            logger.warning(
                "Cloud Safe Mode is pausing admission of a new Music Batch song: "
                + "; ".join(reasons)
            )
            self.sleep_fn(self.thresholds.poll_interval_seconds)
