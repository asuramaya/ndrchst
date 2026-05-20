"""Host metrics for the System page — stdlib only (no psutil dep).

Reads /proc on Linux for load + memory + uptime and falls back to None
on platforms that don't expose it. Disk usage comes from shutil, which
is cross-platform.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path


def _loadavg() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except (OSError, AttributeError):
        return None


def _meminfo() -> dict | None:
    """Total/available/used memory in bytes from /proc/meminfo (Linux)."""
    p = Path("/proc/meminfo")
    if not p.exists():
        return None
    vals: dict[str, int] = {}
    for line in p.read_text().splitlines():
        key, _, rest = line.partition(":")
        fields = rest.strip().split()
        if fields and fields[0].isdigit():
            vals[key] = int(fields[0]) * 1024  # /proc/meminfo is in kB
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    if total is None:
        return None
    used = total - avail if avail is not None else None
    return {
        "total": total,
        "available": avail,
        "used": used,
        "used_pct": round(used / total * 100, 1) if used is not None and total else None,
    }


def _uptime_seconds() -> float | None:
    p = Path("/proc/uptime")
    if not p.exists():
        return None
    try:
        return float(p.read_text().split()[0])
    except (ValueError, IndexError):
        return None


def disk_usage(path: Path) -> dict | None:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return None
    return {
        "total": u.total,
        "used": u.used,
        "free": u.free,
        "used_pct": round(u.used / u.total * 100, 1) if u.total else None,
    }


def host_metrics() -> dict:
    la = _loadavg()
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpu_count": os.cpu_count(),
        "loadavg": list(la) if la else None,
        "memory": _meminfo(),
        "uptime_seconds": _uptime_seconds(),
        "python": sys.version.split()[0],
    }


def human_bytes(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024 or unit == "PB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_duration(secs: float | int | None) -> str:
    if secs is None:
        return "—"
    secs = int(secs)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)
