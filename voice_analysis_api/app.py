"""FastAPI application for the M2 task lifecycle."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from voice_analysis_engine.contracts import SegmentDocument

from .backend import AnalysisBackend, LocalAnalysisBackend
from .asr import AliyunAsrProvider, AliyunCredentials, AsrError, TencentAsrProvider, TencentCredentials, parse_sensitive_json
from .config import ApiConfig, load_api_config
from .store import TaskStore, now_ms


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "schema_version", "task_id", "status", "stage", "result_status", "created_at_ms",
        "updated_at_ms", "started_at_ms", "finished_at_ms", "expired_at_ms", "error",
        "input_mode", "transcript_source", "asr_provider",
    }
    return {key: task[key] for key in keys if key in task and task[key] is not None}


async def _upload_digest(upload: UploadFile, maximum: int) -> tuple[str, int]:
    def consume() -> tuple[str, int]:
        upload.file.seek(0)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ValueError("upload too large")
            digest.update(chunk)
        upload.file.seek(0)
        return digest.hexdigest(), total

    return await asyncio.to_thread(consume)


async def _segments_bytes(upload: UploadFile, maximum: int) -> bytes:
    raw = await upload.read(maximum + 1)
    if len(raw) > maximum:
        raise HTTPException(status_code=413, detail={"code": "SEGMENTS_TOO_LARGE", "message": "segments document is too large"})
    try:
        document = SegmentDocument.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail={"code": "SEGMENTS_INVALID", "message": str(exc)[:1000]}) from exc
    return json.dumps(document.model_dump(mode="json", exclude_unset=True), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _cleanup_loop(app: FastAPI) -> None:
    while True:
        try:
            await app.state.store.cleanup(app.state.config.retention_seconds, app.state.config.expired_metadata_seconds)
        except Exception:  # noqa: BLE001
            app.state.cleanup_failures += 1
        await asyncio.sleep(app.state.config.cleanup_interval_seconds)


def create_app(config: ApiConfig | None = None, backend: AnalysisBackend | None = None) -> FastAPI:
    cfg = config or load_api_config()
    store = TaskStore(cfg.task_root)
    selected_backend = backend or LocalAnalysisBackend(
        store,
        cfg.backend_slots,
        ready_urls=(cfg.embedding_ready_url, cfg.refine_ready_url),
        shutdown_grace_seconds=cfg.shutdown_grace_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await selected_backend.recover()
        cleanup_task = asyncio.create_task(_cleanup_loop(app), name="task-cleanup")
        try:
            yield
        finally:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
            await selected_backend.close()

    app = FastAPI(title="Voice Analysis Task API", version="1.0.0", lifespan=lifespan)
    app.state.config = cfg
    app.state.store = store
    app.state.backend = selected_backend
    app.state.cleanup_failures = 0
    app.state.create_lock = asyncio.Lock()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        tasks = store.list_tasks()
        return {
            "status": "ok",
            "backend": selected_backend.summary(),
            "tasks": {"retained": len(tasks), "failed": sum(t.get("status") == "failed" for t in tasks)},
            "cleanup_failures": app.state.cleanup_failures,
        }

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        ready_state = {"task_root": False, "voice_embedding": False, "window_refine": False}
        try:
            cfg.task_root.mkdir(parents=True, exist_ok=True)
            probe = cfg.task_root / ".ready-probe"
            probe.write_bytes(b"")
            probe.unlink()
            ready_state["task_root"] = True
        except OSError:
            pass
        async with httpx.AsyncClient(timeout=2.0) as client:
            for key, url in (("voice_embedding", cfg.embedding_ready_url), ("window_refine", cfg.refine_ready_url)):
                try:
                    ready_state[key] = (await client.get(url)).status_code == 200
                except httpx.HTTPError:
                    ready_state[key] = False
        status = 200 if all(ready_state.values()) else 503
        return JSONResponse({"status": "ready" if status == 200 else "not_ready", "components": ready_state}, status_code=status)

    @app.post("/v1/asr/validate")
    async def validate_asr(
        asr_provider: str = Form(...), asr_credentials: str = Form(...), asr_options: str = Form("{}"),
    ) -> dict[str, str]:
        if asr_provider not in {"tencent", "aliyun"}:
            raise HTTPException(status_code=400, detail={"code": "ASR_PROVIDER_INVALID", "message": "unsupported ASR provider"})
        try:
            credentials = parse_sensitive_json(asr_credentials, field="asr_credentials")
            options = parse_sensitive_json(asr_options, field="asr_options")
            provider = TencentAsrProvider() if asr_provider == "tencent" else AliyunAsrProvider()
            await provider.validate_credentials(credentials, options)
            return {"status": "valid", "provider": asr_provider}
        except AsrError as exc:
            raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
        finally:
            if "credentials" in locals():
                credentials.clear()

    @app.post("/v1/tasks", status_code=202)
    async def create_task(
        request: Request,
        audio: UploadFile = File(...),
        segments: UploadFile | None = File(None),
        input_mode: str = Form("provided_transcript"),
        asr_provider: str | None = Form(None),
        asr_credentials: str | None = Form(None),
        asr_options: str | None = Form(None),
        deadline_sec: float | None = Form(None),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if input_mode not in {"provided_transcript", "cloud_asr"}:
            raise HTTPException(status_code=400, detail={"code": "INPUT_MODE_INVALID", "message": "unsupported input mode"})
        if deadline_sec is not None and (not math.isfinite(deadline_sec) or deadline_sec <= 0):
            raise HTTPException(status_code=400, detail={"code": "DEADLINE_INVALID", "message": "deadline_sec must be greater than zero"})
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 200:
            raise HTTPException(status_code=400, detail={"code": "IDEMPOTENCY_KEY_INVALID", "message": "Idempotency-Key must contain 1 to 200 characters"})
        sensitive: dict[str, Any] | None = None
        options: dict[str, Any] = {}
        if input_mode == "provided_transcript":
            if segments is None:
                raise HTTPException(status_code=400, detail={"code": "SEGMENTS_REQUIRED", "message": "segments are required"})
            if asr_provider or asr_credentials or asr_options:
                raise HTTPException(status_code=400, detail={"code": "INPUT_FIELDS_CONFLICT", "message": "ASR fields are not allowed with a provided transcript"})
            segments_raw = await _segments_bytes(segments, cfg.max_segments_bytes)
        else:
            if segments is not None:
                raise HTTPException(status_code=400, detail={"code": "INPUT_FIELDS_CONFLICT", "message": "segments are not allowed with cloud ASR"})
            if asr_provider not in {"tencent", "aliyun"}:
                raise HTTPException(status_code=400, detail={"code": "ASR_PROVIDER_INVALID", "message": "unsupported ASR provider"})
            try:
                credentials = parse_sensitive_json(asr_credentials, field="asr_credentials")
                options = parse_sensitive_json(asr_options or "{}", field="asr_options")
                model = TencentCredentials if asr_provider == "tencent" else AliyunCredentials
                model.model_validate(credentials)
            except (AsrError, ValidationError) as exc:
                raise HTTPException(status_code=400, detail={"code": "ASR_CREDENTIALS_INVALID", "message": "ASR credentials or options are invalid"}) from exc
            sensitive = {"credentials": credentials, "options": options}
            if asr_provider == "tencent" and not options.get("voice_format"):
                suffix = Path(audio.filename or "").suffix.lower().lstrip(".")
                options["voice_format"] = suffix if suffix in {"wav", "pcm", "ogg-opus", "speex", "silk", "mp3", "m4a", "aac", "amr"} else "wav"
            segments_raw = None
        try:
            audio_digest, audio_size = await _upload_digest(audio, cfg.max_audio_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail={"code": "AUDIO_TOO_LARGE", "message": "audio is too large"}) from exc
        if audio_size == 0:
            raise HTTPException(status_code=400, detail={"code": "AUDIO_EMPTY", "message": "audio must not be empty"})
        digest_material = segments_raw if segments_raw is not None else json.dumps({"provider": asr_provider, "options": options}, sort_keys=True, ensure_ascii=False).encode()
        input_digest = hashlib.sha256(audio_digest.encode("ascii") + b":" + input_mode.encode() + b":" + digest_material).hexdigest()
        async with app.state.create_lock:
            if idempotency_key:
                existing = store.find_idempotency(idempotency_key)
                if existing is not None:
                    if existing.get("input_digest") != input_digest:
                        if sensitive is not None:
                            sensitive.clear()
                        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "idempotency key was used with different input"})
                    if sensitive is not None:
                        sensitive.clear()
                    return JSONResponse(_public_task(existing), status_code=200)

            if not await selected_backend.ready():
                raise HTTPException(status_code=429, detail={"code": "BACKEND_NOT_READY", "message": "analysis backend is not ready"}, headers={"Retry-After": "1"})
            task_id = uuid.uuid4().hex
            reservation = await selected_backend.reserve(task_id)
            if reservation is None:
                raise HTTPException(status_code=429, detail={"code": "BACKEND_BUSY", "message": "analysis backend is not accepting a task"}, headers={"Retry-After": "1"})
            created = now_ms()
            task = {
                "schema_version": "voice_analysis_task_v1",
                "task_id": task_id,
                "status": "queued",
                "stage": None,
                "created_at_ms": created,
                "updated_at_ms": created,
                "deadline_epoch_ms": int(time.time() * 1000 + deadline_sec * 1000) if deadline_sec is not None else None,
                "idempotency_key": idempotency_key,
                "input_digest": input_digest,
                "input_mode": input_mode,
                "asr_provider": asr_provider,
                "transcript_source": {"mode": "provided_transcript"} if input_mode == "provided_transcript" else {"mode": "cloud_asr", "provider": asr_provider},
            }
            try:
                await store.create(task, audio, segments_raw)
                if sensitive is None:
                    await selected_backend.start(reservation)
                else:
                    await selected_backend.start(reservation, sensitive)
            except Exception:  # noqa: BLE001
                await selected_backend.release(reservation)
                if sensitive is not None:
                    sensitive.clear()
                try:
                    await store.delete(task_id)
                except KeyError:
                    pass
                raise HTTPException(status_code=500, detail={"code": "TASK_DELIVERY_FAILED", "message": "task could not be delivered"})
        return JSONResponse(_public_task(task), status_code=202)

    def load_task(task_id: str) -> dict[str, Any]:
        try:
            return store.read(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "task not found"}) from exc

    @app.get("/v1/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> dict[str, Any]:
        return _public_task(load_task(task_id))

    @app.get("/v1/tasks/{task_id}/result")
    async def get_result(task_id: str, request: Request):
        task = load_task(task_id)
        if task.get("status") == "expired":
            raise HTTPException(status_code=410, detail={"code": "TASK_EXPIRED", "message": "task has expired"})
        if task.get("status") != "succeeded":
            raise HTTPException(status_code=409, detail={"code": "RESULT_NOT_READY", "message": "task result is not available"})
        path = store.task_dir(task_id) / "result" / "result.json"
        if not path.is_file():
            raise HTTPException(status_code=500, detail={"code": "RESULT_MISSING", "message": "task result is missing"})
        return FileResponse(path, media_type="application/json", filename="result.json")

    @app.get("/v1/tasks/{task_id}/audio")
    async def get_audio(task_id: str):
        task = load_task(task_id)
        if task.get("status") == "expired":
            raise HTTPException(status_code=410, detail={"code": "TASK_EXPIRED", "message": "task has expired"})
        path = store.task_dir(task_id) / "input" / "audio"
        if not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "AUDIO_NOT_FOUND", "message": "task audio is not available"})
        return FileResponse(path, media_type="application/octet-stream")

    @app.get("/v1/tasks/{task_id}/exports/{format_name}")
    async def get_export(task_id: str, format_name: str, request: Request):
        task = load_task(task_id)
        if task.get("status") == "expired":
            raise HTTPException(status_code=410, detail={"code": "TASK_EXPIRED", "message": "task has expired"})
        if task.get("status") != "succeeded":
            raise HTTPException(status_code=409, detail={"code": "RESULT_NOT_READY", "message": "task result is not available"})
        names = {"json": "result.json", "txt": "transcript.txt", "srt": "transcript.srt", "vtt": "transcript.vtt"}
        if format_name not in names:
            raise HTTPException(status_code=404, detail={"code": "EXPORT_NOT_FOUND", "message": "export format is not available"})
        path = store.task_dir(task_id) / "result" / names[format_name]
        if not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "EXPORT_NOT_FOUND", "message": "export is not available"})
        return FileResponse(path, filename=names[format_name])

    @app.post("/v1/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
        task = load_task(task_id)
        if task.get("status") in {"succeeded", "failed", "cancelled", "expired"}:
            raise HTTPException(status_code=409, detail={"code": "TASK_NOT_CANCELLABLE", "message": "task is already terminal"})
        requested = await store.request_cancel(task_id)
        if requested is None:
            raise HTTPException(status_code=409, detail={"code": "TASK_NOT_CANCELLABLE", "message": "task is already terminal"})
        accepted = await selected_backend.cancel(task_id)
        if not accepted:
            refreshed = store.read(task_id)
            if refreshed.get("status") == "cancelled":
                return _public_task(refreshed)
            raise HTTPException(status_code=409, detail={"code": "TASK_NOT_CANCELLABLE", "message": "backend no longer owns the task"})
        return _public_task(store.read(task_id))

    @app.delete("/v1/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: str, request: Request) -> None:
        task = load_task(task_id)
        if task.get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail={"code": "TASK_ACTIVE", "message": "cancel the active task before deleting it"})
        await store.delete(task_id)

    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

        @app.get("/{page_path:path}", include_in_schema=False)
        async def web_app(page_path: str):
            candidate = (web_dist / page_path).resolve()
            if candidate.is_file() and web_dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")

    return app
