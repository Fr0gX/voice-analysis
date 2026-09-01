from __future__ import annotations

import json

import pytest

from voice_analysis_engine.config import load_analysis_config
from voice_analysis_engine.contracts import SegmentDocument
from voice_analysis_engine.errors import EngineError
from voice_analysis_engine.windows import discover_candidates


def test_locked_algorithm_cannot_be_overridden(tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("algorithm:\n  clustering:\n    maximum_k: 9\n", encoding="utf-8")
    with pytest.raises(EngineError, match="locked or unknown"):
        load_analysis_config(overlay)


def test_product_configuration_can_be_overridden(tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("exports:\n  include_confidence: true\n", encoding="utf-8")
    config = load_analysis_config(overlay)
    assert config.section("exports")["include_confidence"] is True
    assert config.section("algorithm")["clustering"]["maximum_k"] == 6


def test_public_configuration_redacts_credentials_and_url_userinfo(tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "components:\n"
        "  voice_embedding:\n"
        "    base_url: https://user:password@example.test/embed?token=secret&mode=fast\n"
        "    api_key: raw-secret\n",
        encoding="utf-8",
    )

    snapshot = load_analysis_config(overlay).public_snapshot()

    component = snapshot["components"]["voice_embedding"]
    assert component["api_key"] == "<redacted>"
    assert "user" not in component["base_url"]
    assert "password" not in component["base_url"]
    assert "secret" not in component["base_url"]
    assert "mode=fast" in component["base_url"]


def test_public_configuration_records_custom_temporary_root_without_host_path(tmp_path):
    custom_root = tmp_path / "private" / "analysis-runs"
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "runtime:\n"
        f"  temporary_root: {json.dumps(str(custom_root))}\n",
        encoding="utf-8",
    )

    snapshot = load_analysis_config(overlay).public_snapshot()

    assert snapshot["runtime"]["temporary_root"] == "analysis-runs"


def test_segment_contract_rejects_duplicates_and_invalid_confidence():
    with pytest.raises(ValueError):
        SegmentDocument.model_validate({
            "segments": [
                {"id": "same", "start_ms": 0, "end_ms": 1000, "text": "a"},
                {"id": "same", "start_ms": 1000, "end_ms": 2000, "text": "b"},
            ]
        })
    with pytest.raises(ValueError):
        SegmentDocument.model_validate({
            "segments": [{"id": "a", "start_ms": 0, "end_ms": 1000, "text": "a", "confidence": 2}]
        })


def test_candidate_discovery_is_time_only_and_ignores_asr_speaker():
    segments = [
        {"input_index": 0, "start_ms": 0, "end_ms": 1000, "text": "a", "confidence": None, "speaker": "A"},
        {"input_index": 1, "start_ms": 1200, "end_ms": 2200, "text": "b", "confidence": None, "speaker": "B"},
    ]
    candidates, audit = discover_candidates(segments, max_join_gap_ms=500)
    assert len(candidates) == 1
    assert candidates[0]["source_segment_indices"] == [0, 1]
    assert audit["asr_speaker_used"] is False
    assert "speaker" not in json.dumps(candidates)
