"""Best-effort process memory guard; M2 may replace the detected budget."""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AnalysisConfig
from .errors import EngineError


@dataclass(frozen=True)
class MemorySnapshot:
    budget_bytes: int | None
    rss_bytes: int | None
    target_bytes: int | None
    hard_limit_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_bytes": self.budget_bytes,
            "rss_bytes": self.rss_bytes,
            "target_bytes": self.target_bytes,
            "hard_limit_bytes": self.hard_limit_bytes,
        }


class MemoryGuard:
    def __init__(self, cfg: AnalysisConfig):
        runtime = cfg.section("runtime")
        configured = runtime.get("memory_budget_bytes")
        self.budget = int(configured) if configured else _memory_budget()
        self.target_ratio = float(runtime["memory_target_ratio"])
        self.hard_ratio = float(runtime["memory_hard_limit_ratio"])

    def check(self, stage: str) -> MemorySnapshot:
        rss = _rss_bytes()
        target = int(self.budget * self.target_ratio) if self.budget else None
        hard = int(self.budget * self.hard_ratio) if self.budget else None
        if rss is not None and hard is not None and rss >= hard:
            raise EngineError(
                "MEMORY_HARD_LIMIT",
                "analysis process reached the configured memory hard limit",
                stage,
                30,
                True,
            )
        return MemorySnapshot(self.budget, rss, target, hard)

    def dense_nme_budget(self, configured_max_bytes: int) -> int:
        snapshot = self.check("speaker_clustering")
        if snapshot.rss_bytes is None or snapshot.target_bytes is None:
            return int(configured_max_bytes)
        headroom = max(0, snapshot.target_bytes - snapshot.rss_bytes)
        return max(1024 * 1024, min(int(configured_max_bytes), headroom // 4))


def _memory_budget() -> int | None:
    cgroup = Path("/sys/fs/cgroup/memory.max")
    if cgroup.is_file():
        try:
            raw = cgroup.read_text(encoding="ascii").strip()
            if raw != "max":
                return int(raw)
        except (OSError, ValueError):
            pass
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def _rss_bytes() -> int | None:
    status = Path("/proc/self/status")
    if status.is_file():
        try:
            for line in status.read_text(encoding="ascii").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
    if os.name == "nt":
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
    return None
