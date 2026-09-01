"""Versioned public contracts for the M1 analysis engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SegmentInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    confidence: float | None = None
    speaker: str | int | None = None

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "SegmentInput":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class SegmentDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["voice_analysis_input_v1"] = "voice_analysis_input_v1"
    segments: list[SegmentInput] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ids(self) -> "SegmentDocument":
        ids = [item.id for item in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("segment ids must be unique")
        return self


class AnalysisRequest(BaseModel):
    audio_path: Path
    document: SegmentDocument
    output_dir: Path | None = None
    config_overlay: Path | None = None
    deadline_epoch_ms: int | None = None


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal["voice_analysis_result_v1"] = "voice_analysis_result_v1"
    run_id: str
    status: Literal["success", "partial"]
    audio: dict[str, Any]
    configuration: dict[str, Any]
    models: dict[str, Any]
    speakers: list[dict[str, Any]]
    segments: list[dict[str, Any]]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    components: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
