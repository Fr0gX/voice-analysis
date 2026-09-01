from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voice_analysis_api.asr import AliyunAsrProvider, AsrError, TencentAsrProvider


def test_tencent_normalizes_timed_segments_without_using_provider_speaker(tmp_path):
    async def transport(path, credentials, options):
        assert credentials.secret_key.get_secret_value() == "key"
        return {"segments": [{"start_ms": 0, "end_ms": 500, "text": "你好", "speaker": "vendor-1", "confidence": .9}]}

    async def scenario():
        provider = TencentAsrProvider(transport=transport)
        value = await provider.transcribe(tmp_path / "audio", {"secret_id": "id", "secret_key": "key", "app_id": "app"}, {"engine_model": "16k_zh"}, cancelled=lambda: False, deadline_epoch_ms=None)
        assert value.document.segments[0].speaker == "vendor-1"
        assert value.document.segments[0].model_extra["provider_source"]["speaker"] == "vendor-1"
        assert value.source["provider"] == "tencent"
    asyncio.run(scenario())


def test_aliyun_retries_retryable_errors(tmp_path):
    attempts = 0
    async def transport(path, credentials, options):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AsrError("ASR_RATE_LIMITED", "busy", retryable=True)
        return {"segments": [{"start_ms": 0, "end_ms": 1, "text": "x"}]}
    async def scenario():
        provider = AliyunAsrProvider(transport=transport)
        await provider.transcribe(tmp_path / "audio", {"access_key_id": "id", "access_key_secret": "key", "app_key": "app"}, {}, cancelled=lambda: False, deadline_epoch_ms=None)
    asyncio.run(scenario())
    assert attempts == 2


def test_invalid_timeline_is_explicit(tmp_path):
    async def transport(path, credentials, options):
        return {"segments": [{"start_ms": 2, "end_ms": 1, "text": "x"}]}
    async def scenario():
        with pytest.raises(AsrError) as caught:
            await TencentAsrProvider(transport=transport).transcribe(tmp_path / "audio", {"secret_id": "id", "secret_key": "key", "app_id": "app"}, {}, cancelled=lambda: False, deadline_epoch_ms=None)
        assert caught.value.code == "ASR_RESULT_INVALID"
    asyncio.run(scenario())
