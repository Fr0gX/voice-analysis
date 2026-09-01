"""Cloud ASR provider boundary with ephemeral credentials."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from voice_analysis_engine.contracts import SegmentDocument


class AsrError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class TencentCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret_id: SecretStr
    secret_key: SecretStr
    app_id: str = Field(min_length=1)


class AliyunCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_key_id: SecretStr
    access_key_secret: SecretStr
    app_key: str = Field(min_length=1)


@dataclass
class AsrTranscript:
    document: SegmentDocument
    source: dict[str, Any]


class AsrProvider(Protocol):
    async def transcribe(
        self, audio_path: Path, credentials: dict[str, Any], options: dict[str, Any],
        *, cancelled: Callable[[], bool], deadline_epoch_ms: int | None,
    ) -> AsrTranscript: ...
    async def validate_credentials(self, credentials: dict[str, Any], options: dict[str, Any]) -> None: ...


Transport = Callable[[Path, BaseModel, dict[str, Any]], Awaitable[dict[str, Any]]]


class _ProviderBase:
    name = ""
    credential_model: type[BaseModel]

    def __init__(self, transport: Transport | None = None, retries: int = 2) -> None:
        self.transport = transport
        self.retries = max(0, retries)

    async def transcribe(self, audio_path, credentials, options, *, cancelled, deadline_epoch_ms):
        try:
            secret = self.credential_model.model_validate(credentials)
        except ValidationError as exc:
            raise AsrError("ASR_CREDENTIALS_INVALID", "ASR credentials are incomplete or invalid") from exc
        if not isinstance(options, dict):
            raise AsrError("ASR_OPTIONS_INVALID", "ASR options must be an object")
        transport = self.transport or self._official_transport
        for attempt in range(self.retries + 1):
            if cancelled():
                raise AsrError("TASK_CANCELLED", "task cancellation requested")
            if deadline_epoch_ms is not None and int(__import__("time").time() * 1000) >= deadline_epoch_ms:
                raise AsrError("DEADLINE_EXCEEDED", "ASR deadline exceeded")
            try:
                raw = await transport(audio_path, secret, options)
                return self._normalize(raw, options)
            except AsrError as exc:
                if not exc.retryable or attempt == self.retries:
                    raise
                await asyncio.sleep(min(2 ** attempt, 2))
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == self.retries:
                    raise AsrError("ASR_NETWORK_ERROR", "ASR provider is unavailable", retryable=True) from exc
                await asyncio.sleep(min(2 ** attempt, 2))
        raise AsrError("ASR_INTERNAL_ERROR", "ASR request failed")

    async def validate_credentials(self, credentials: dict[str, Any], options: dict[str, Any]) -> None:
        try:
            self.credential_model.model_validate(credentials)
        except ValidationError as exc:
            raise AsrError("ASR_CREDENTIALS_INVALID", "ASR credentials are incomplete or invalid") from exc

    async def _official_transport(self, audio_path: Path, credentials: BaseModel, options: dict[str, Any]) -> dict[str, Any]:
        raise AsrError(
            "ASR_SDK_NOT_CONFIGURED",
            f"{self.name} official transport is unavailable; install and configure the locked provider SDK",
        )

    def _normalize(self, raw: dict[str, Any], options: dict[str, Any]) -> AsrTranscript:
        items = raw.get("segments")
        if not isinstance(items, list) or not items:
            raise AsrError("ASR_EMPTY_RESULT", "ASR provider returned no timed segments")
        segments = []
        for index, item in enumerate(items):
            try:
                start_ms = int(item["start_ms"])
                end_ms = int(item["end_ms"])
                text = str(item.get("text", ""))
            except (KeyError, TypeError, ValueError) as exc:
                raise AsrError("ASR_RESULT_INVALID", "ASR provider returned an invalid time line") from exc
            segment: dict[str, Any] = {
                "id": str(item.get("id") or f"asr-{index + 1}"),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "provider_source": {"provider": self.name},
            }
            if item.get("confidence") is not None:
                segment["confidence"] = float(item["confidence"])
            if item.get("speaker") is not None:
                segment["speaker"] = item["speaker"]
                segment["provider_source"]["speaker"] = item["speaker"]
            segments.append(segment)
        try:
            document = SegmentDocument.model_validate({
                "schema_version": "voice_analysis_input_v1",
                "segments": segments,
                "metadata": {"transcript_source": {"mode": "cloud_asr", "provider": self.name}},
            })
        except ValidationError as exc:
            raise AsrError("ASR_RESULT_INVALID", "ASR provider returned an invalid time line") from exc
        source = {
            "mode": "cloud_asr", "provider": self.name,
            "model": str(options.get("model") or options.get("engine_model") or "default"),
            "normalization_version": "cloud_asr_v1",
        }
        return AsrTranscript(document=document, source=source)


class TencentAsrProvider(_ProviderBase):
    name = "tencent"
    credential_model = TencentCredentials

    async def validate_credentials(self, credentials: dict[str, Any], options: dict[str, Any]) -> None:
        await super().validate_credentials(credentials, options)
        secret = TencentCredentials.model_validate(credentials)
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        handle.close()
        path = Path(handle.name)
        try:
            try:
                await self._official_transport(path, secret, options)
            except AsrError as exc:
                if exc.code != "ASR_AUDIO_EMPTY":
                    raise
        finally:
            path.unlink(missing_ok=True)

    async def _official_transport(self, audio_path: Path, credentials: TencentCredentials, options: dict[str, Any]) -> dict[str, Any]:
        if audio_path.stat().st_size > 100 * 1024 * 1024:
            raise AsrError("ASR_INPUT_TOO_LARGE", "Tencent Flash ASR accepts at most 100 MiB")
        params = {
            "engine_type": str(options.get("engine_model") or "16k_zh"),
            "secretid": credentials.secret_id.get_secret_value(),
            "timestamp": str(int(time.time())),
            "voice_format": str(options.get("voice_format") or "wav"),
            "speaker_diarization": "1",
            "word_info": "1",
            "first_channel_only": "1",
            "filter_punc": "0",
        }
        hotwords = options.get("hotword_list")
        if hotwords:
            params["hotword_list"] = str(hotwords)
        query = urlencode(sorted(params.items()))
        host = "asr.cloud.tencent.com"
        path = f"/asr/flash/v1/{credentials.app_id}"
        signing = f"POST{host}{path}?{query}".encode()
        signature = base64.b64encode(hmac.new(credentials.secret_key.get_secret_value().encode(), signing, hashlib.sha1).digest()).decode()
        timeout = float(options.get("timeout_sec") or 180)
        async with httpx.AsyncClient(timeout=timeout) as client:
            with audio_path.open("rb") as body:
                response = await client.post(
                    f"https://{host}{path}?{query}", content=body,
                    headers={"Authorization": signature, "Content-Type": "application/octet-stream"},
                )
        if response.status_code in {429, 500, 502, 503, 504}:
            raise AsrError("ASR_PROVIDER_BUSY", "Tencent ASR is temporarily unavailable", retryable=True)
        try:
            value = response.json()
        except ValueError as exc:
            raise AsrError("ASR_PROTOCOL_ERROR", "Tencent ASR returned invalid JSON", retryable=response.status_code >= 500) from exc
        code = int(value.get("code", response.status_code))
        if code != 0:
            mapping = {4002: "ASR_AUTH_FAILED", 4003: "ASR_SERVICE_DISABLED", 4004: "ASR_QUOTA_EXHAUSTED", 4005: "ASR_ACCOUNT_SUSPENDED", 4006: "ASR_RATE_LIMITED", 4011: "ASR_INPUT_TOO_LARGE", 4012: "ASR_AUDIO_EMPTY"}
            raise AsrError(mapping.get(code, "ASR_PROVIDER_ERROR"), f"Tencent ASR rejected the request (code {code})", retryable=code >= 5000 or code == 4006)
        segments = []
        for channel in value.get("flash_result") or []:
            for sentence in channel.get("sentence_list") or []:
                segments.append({
                    "start_ms": sentence.get("start_time"), "end_ms": sentence.get("end_time"),
                    "text": sentence.get("text", ""), "speaker": sentence.get("speaker_id"),
                })
        return {"segments": segments}


class AliyunAsrProvider(_ProviderBase):
    name = "aliyun"
    credential_model = AliyunCredentials

    async def validate_credentials(self, credentials: dict[str, Any], options: dict[str, Any]) -> None:
        await super().validate_credentials(credentials, options)
        secret = AliyunCredentials.model_validate(credentials)
        await asyncio.to_thread(self._create_token, secret, str(options.get("region") or "cn-shanghai"))

    @staticmethod
    def _create_token(credentials: AliyunCredentials, region: str) -> str:
        try:
            from aliyunsdkcore.client import AcsClient
            from aliyunsdknls_cloud_meta.request.v20180518.CreateTokenRequest import CreateTokenRequest
        except ImportError as exc:
            raise AsrError("ASR_SDK_NOT_CONFIGURED", "Aliyun ASR SDK dependencies are not installed") from exc
        client = AcsClient(credentials.access_key_id.get_secret_value(), credentials.access_key_secret.get_secret_value(), region)
        try:
            return json.loads(client.do_action_with_exception(CreateTokenRequest()))["Token"]["Id"]
        except Exception as exc:
            raise AsrError("ASR_AUTH_FAILED", "Aliyun ASR credentials could not create a token") from exc

    async def _official_transport(self, audio_path: Path, credentials: AliyunCredentials, options: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._transcribe_sync, audio_path, credentials, options)

    @staticmethod
    def _transcribe_sync(audio_path: Path, credentials: AliyunCredentials, options: dict[str, Any]) -> dict[str, Any]:
        try:
            import nls
        except ImportError as exc:
            raise AsrError("ASR_SDK_NOT_CONFIGURED", "Aliyun ASR SDK dependencies are not installed") from exc
        region = str(options.get("region") or "cn-shanghai")
        token_value = AliyunAsrProvider._create_token(credentials, region)
        pcm_path = audio_path.with_name(".asr-normalized.pcm")
        sentences: list[dict[str, Any]] = []
        errors: list[str] = []

        def on_sentence_end(message, *_):
            try:
                payload = json.loads(message).get("payload") or {}
                start = int(payload.get("begin_time", payload.get("start_time", 0)))
                end = int(payload.get("time", payload.get("end_time", start)))
                sentences.append({"start_ms": start, "end_ms": end, "text": payload.get("result", "")})
            except Exception:
                errors.append("invalid sentence response")

        def on_error(message, *_):
            errors.append("provider error")

        transcriber = nls.NlsSpeechTranscriber(
            url=f"wss://nls-gateway-{region}.aliyuncs.com/ws/v1",
            token=token_value, appkey=credentials.app_key,
            on_sentence_end=on_sentence_end, on_error=on_error,
        )
        try:
            completed = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(audio_path), "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm_path)],
                capture_output=True, timeout=float(options.get("decode_timeout_sec") or 600), check=False,
            )
            if completed.returncode != 0:
                raise AsrError("ASR_AUDIO_INVALID", "audio could not be normalized for Aliyun ASR")
            if not transcriber.start(aformat="pcm", sample_rate=16000, ch=1, enable_punctuation_prediction=True, enable_inverse_text_normalization=True):
                raise AsrError("ASR_PROVIDER_ERROR", "Aliyun ASR session could not start", retryable=True)
            with pcm_path.open("rb") as source:
                while chunk := source.read(3200):
                    if not transcriber.send_audio(chunk):
                        raise AsrError("ASR_NETWORK_ERROR", "Aliyun ASR audio upload failed", retryable=True)
            transcriber.stop(timeout=int(options.get("timeout_sec") or 60))
            if errors:
                raise AsrError("ASR_PROVIDER_ERROR", "Aliyun ASR returned an error")
            return {"segments": sentences}
        finally:
            try:
                transcriber.shutdown()
            except Exception:
                pass
            pcm_path.unlink(missing_ok=True)


def parse_sensitive_json(value: str | None, *, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError as exc:
        raise AsrError(f"{field.upper()}_INVALID", f"{field} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AsrError(f"{field.upper()}_INVALID", f"{field} must be an object")
    return parsed
