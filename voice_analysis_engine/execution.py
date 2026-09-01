"""Stage execution boundary used by M1 inline runs and the future M2 scheduler."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, Protocol, TypeVar


class ResourceClass(StrEnum):
    AUDIO_IO = "audio_io"
    WINDOW_REFINE = "window_refine"
    VOICE_EMBEDDING = "voice_embedding"
    CPU_CLUSTER = "cpu_cluster"
    EXPORT_IO = "export_io"


@dataclass(frozen=True)
class StageWork:
    stage: str
    resource: ResourceClass
    estimated_memory_bytes: int = 0
    blocking: bool = False


T = TypeVar("T")


class StageExecutor(Protocol):
    async def run(self, work: StageWork, operation: Callable[[], T | Awaitable[T]]) -> T: ...


class InlineStageExecutor:
    """Run one analysis while releasing the resource boundary after every stage."""

    async def run(self, work: StageWork, operation: Callable[[], T | Awaitable[T]]) -> T:
        if work.blocking:
            result: Any = await asyncio.to_thread(operation)
        else:
            result = operation()
        if inspect.isawaitable(result):
            return await result
        return result
