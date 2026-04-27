"""Background resource sampler for NORA observability.

Samples CPU, RAM, disk, and GPU utilization every N seconds and records
to MetricsStore. Reads from /proc directly to avoid adding psutil as a
dependency (Linux-only).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Interval between samples
_DEFAULT_INTERVAL = 30


async def start_resource_sampler(
    metrics_store,
    interval: int = _DEFAULT_INTERVAL,
    data_dir: str = "data",
) -> asyncio.Task:
    """Start the background sampler and return its task handle."""
    task = asyncio.create_task(
        _sampler_loop(metrics_store, interval, data_dir)
    )
    return task


async def _sampler_loop(
    metrics_store,
    interval: int,
    data_dir: str,
) -> None:
    logger.info("Resource sampler started (interval=%ds)", interval)
    while True:
        try:
            await _sample_once(metrics_store, data_dir)
        except asyncio.CancelledError:
            logger.info("Resource sampler cancelled")
            return
        except Exception as exc:
            logger.debug("Resource sampler error: %s", exc)
        await asyncio.sleep(interval)


async def _sample_once(metrics_store, data_dir: str) -> None:
    from core.src.web.metrics import MetricRecord

    records: list[MetricRecord] = []
    ts = ""  # will be set by record_batch from _now_iso

    # CPU utilization from /proc/stat
    cpu = _read_cpu_percent()
    if cpu is not None:
        records.append(MetricRecord(
            timestamp="", category="resource", name="cpu_percent",
            value=cpu, unit="percent", tags={},
        ))

    # RAM from /proc/meminfo
    ram_used, ram_total = _read_memory_gb()
    if ram_total > 0:
        records.append(MetricRecord(
            timestamp="", category="resource", name="ram_used_gb",
            value=ram_used, unit="GB", tags={"total_gb": round(ram_total, 1)},
        ))
        ram_pct = (ram_used / ram_total) * 100 if ram_total > 0 else 0
        records.append(MetricRecord(
            timestamp="", category="resource", name="ram_percent",
            value=round(ram_pct, 1), unit="percent",
            tags={"total_gb": round(ram_total, 1)},
        ))

    # Disk usage of data/ directory
    disk_used, disk_total = _read_disk_usage(data_dir)
    if disk_total > 0:
        records.append(MetricRecord(
            timestamp="", category="resource", name="disk_used_gb",
            value=disk_used, unit="GB",
            tags={"path": data_dir, "total_gb": round(disk_total, 1)},
        ))

    # GPU via nvidia-smi
    gpu_info = _read_gpu_info()
    if gpu_info is not None:
        records.append(MetricRecord(
            timestamp="", category="resource", name="gpu_percent",
            value=gpu_info["utilization"], unit="percent",
            tags={"gpu_name": gpu_info.get("name", "")},
        ))
        records.append(MetricRecord(
            timestamp="", category="resource", name="gpu_mem_used_gb",
            value=gpu_info["mem_used_gb"], unit="GB",
            tags={"total_gb": gpu_info.get("mem_total_gb", 0)},
        ))

    # Set timestamps
    from core.src.web.metrics import _now_iso
    ts = _now_iso()
    for r in records:
        r.timestamp = ts

    if records:
        await metrics_store.record_batch(records)


# -- /proc readers -------------------------------------------------------------

# CPU state for delta calculation
_prev_cpu_idle: float = 0
_prev_cpu_total: float = 0


def _read_cpu_percent() -> float | None:
    """Read CPU utilization from /proc/stat using delta between calls."""
    global _prev_cpu_idle, _prev_cpu_total
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        if parts[0] != "cpu":
            return None
        values = [int(v) for v in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
        total = sum(values)

        if _prev_cpu_total == 0:
            _prev_cpu_idle = idle
            _prev_cpu_total = total
            return None  # first call, no delta yet

        d_idle = idle - _prev_cpu_idle
        d_total = total - _prev_cpu_total
        _prev_cpu_idle = idle
        _prev_cpu_total = total

        if d_total == 0:
            return 0.0
        return round((1 - d_idle / d_total) * 100, 1)
    except (OSError, ValueError, IndexError):
        return None


def _read_memory_gb() -> tuple[float, float]:
    """Read RAM from /proc/meminfo. Returns (used_gb, total_gb)."""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    info[key] = int(parts[1])  # in kB
        total_kb = info.get("MemTotal", 0)
        available_kb = info.get("MemAvailable", info.get("MemFree", 0))
        used_kb = total_kb - available_kb
        return (round(used_kb / 1048576, 2), round(total_kb / 1048576, 2))
    except (OSError, ValueError):
        return (0.0, 0.0)


def _read_disk_usage(path: str) -> tuple[float, float]:
    """Read disk usage for a path. Returns (used_gb, total_gb)."""
    try:
        import os
        st = os.statvfs(path if Path(path).exists() else ".")
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        used = total - free
        return (round(used / (1024**3), 2), round(total / (1024**3), 2))
    except (OSError, ValueError):
        return (0.0, 0.0)


def _read_gpu_info() -> dict | None:
    """Read GPU utilization via nvidia-smi. Returns None if unavailable."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            return None
        return {
            "name": parts[0],
            "utilization": float(parts[1]),
            "mem_used_gb": round(float(parts[2]) / 1024, 2),
            "mem_total_gb": round(float(parts[3]) / 1024, 2),
        }
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None
