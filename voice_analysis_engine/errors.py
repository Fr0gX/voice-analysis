"""Stable M1 error taxonomy shared by the engine and CLI."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EngineError(Exception):
    code: str
    message: str
    stage: str
    exit_code: int
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def input_error(code: str, message: str, stage: str = "input") -> EngineError:
    return EngineError(code, message, stage, 10, False)


def component_error(code: str, message: str, stage: str, *, retryable: bool = True) -> EngineError:
    return EngineError(code, message, stage, 20, retryable)


def deadline_error(stage: str) -> EngineError:
    return EngineError("DEADLINE_EXCEEDED", "analysis deadline exceeded", stage, 21, True)


def internal_error(message: str, stage: str = "internal") -> EngineError:
    return EngineError("INTERNAL_ERROR", message, stage, 30, False)
