"""Versioned analysis configuration with an immutable algorithm section."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import input_error
from .sanitization import sanitize_public


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "analysis.yaml"
_OVERRIDABLE = {"input", "components", "failure_policy", "exports", "runtime"}


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise input_error("CONFIG_INVALID", f"cannot read configuration: {exc}", "configuration") from exc
    if not isinstance(value, dict):
        raise input_error("CONFIG_INVALID", "configuration root must be an object", "configuration")
    return value


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


@dataclass(frozen=True)
class AnalysisConfig:
    values: dict[str, Any]
    source_path: Path
    effective_digest: str
    algorithm_digest: str

    def section(self, name: str) -> dict[str, Any]:
        value = self.values.get(name)
        if not isinstance(value, dict):
            raise input_error("CONFIG_INVALID", f"missing configuration section: {name}", "configuration")
        return value

    @property
    def profile_id(self) -> str:
        return str(self.section("profile").get("id") or "")

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = {
            "schema_version": self.values["schema_version"],
            "profile_id": self.profile_id,
            "effective_digest": self.effective_digest,
            "algorithm_profile_id": self.section("algorithm")["profile_id"],
            "algorithm_digest": self.algorithm_digest,
            **{key: sanitize_public(copy.deepcopy(self.values[key]), key) for key in sorted(_OVERRIDABLE)},
        }
        temporary_root = Path(str(self.values["runtime"]["temporary_root"])).resolve()
        try:
            snapshot["runtime"]["temporary_root"] = temporary_root.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            snapshot["runtime"]["temporary_root"] = temporary_root.name or "<path>"
        return snapshot


def load_analysis_config(overlay_path: Path | None = None) -> AnalysisConfig:
    base = _read_yaml(_DEFAULT_CONFIG)
    if int(base.get("schema_version") or 0) != 1:
        raise input_error("CONFIG_INVALID", "analysis config schema_version must be 1", "configuration")
    algorithm = base.get("algorithm")
    if not isinstance(algorithm, dict):
        raise input_error("CONFIG_INVALID", "analysis config is missing locked algorithm section", "configuration")
    merged = copy.deepcopy(base)
    source = _DEFAULT_CONFIG
    if overlay_path is not None:
        source = overlay_path.resolve()
        overlay = _read_yaml(source)
        forbidden = sorted(set(overlay) - _OVERRIDABLE)
        if forbidden:
            raise input_error(
                "CONFIG_LOCKED_OVERRIDE",
                "configuration overlay contains locked or unknown sections: " + ", ".join(forbidden),
                "configuration",
            )
        merged = _deep_merge(base, overlay)

    components = merged["components"]
    components["voice_embedding"]["base_url"] = os.getenv(
        "VOICE_EMBEDDING_BASE_URL", components["voice_embedding"]["base_url"]
    ).rstrip("/")
    components["window_refine"]["base_url"] = os.getenv(
        "WINDOW_REFINE_BASE_URL", components["window_refine"]["base_url"]
    ).rstrip("/")
    temporary_root = Path(str(merged["runtime"]["temporary_root"]))
    if not temporary_root.is_absolute():
        temporary_root = (_REPO_ROOT / temporary_root).resolve()
    merged["runtime"]["temporary_root"] = str(temporary_root)

    target = float(merged["runtime"].get("memory_target_ratio") or 0.0)
    hard = float(merged["runtime"].get("memory_hard_limit_ratio") or 0.0)
    if not 0.0 < target < hard <= 1.0:
        raise input_error(
            "CONFIG_INVALID",
            "memory ratios must satisfy 0 < target < hard <= 1",
            "configuration",
        )
    if merged.get("algorithm") != algorithm:
        raise input_error("CONFIG_LOCKED_OVERRIDE", "algorithm configuration is immutable", "configuration")
    return AnalysisConfig(
        values=merged,
        source_path=source,
        effective_digest=_canonical_digest(merged),
        algorithm_digest=_canonical_digest(algorithm),
    )
