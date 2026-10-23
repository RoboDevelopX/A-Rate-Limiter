"""Describe the machine a benchmark ran on, so numbers come with their context."""

import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _memory_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return None


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def describe(**extra: Any) -> dict[str, Any]:
    """Machine, interpreter and code version, plus any benchmark parameters."""
    return {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "cpu": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "git_commit": _git_commit(),
        **extra,
    }
