# HTTP 契约基线

状态：当前组件契约

核验日期：2026-09-01

8077/8078 除 `/health` 外，请求必须携带：

```http
X-Voice-Analysis-Key: <VOICEANALYSIS_API_KEY>
```

`/health/ready` 同样不需要鉴权。8076 M3 首版仅监听 `127.0.0.1`，不建立用户或业务接口鉴权；不得默认开放到局域网或公网。

## 8077 Voice Embedding

- `GET http://127.0.0.1:8077/health`
- `GET http://127.0.0.1:8077/health/ready`
- `POST http://127.0.0.1:8077/embed`

`/embed` 使用 `multipart/form-data`：`metadata` 为任务、窗口、PCM 偏移和截止时间 JSON；`audio` 为 16 kHz 单声道 signed int16 little-endian PCM。成功项返回 192 维归一化向量，逐项状态为 `success/too_short/invalid_boundary/inference_failed`。

协议错误、请求过大、队列过载、模型未就绪和超时分别使用 HTTP 400、413、429、503、504。

`/health` 始终返回组件状态；`/health/ready` 只有在真实 ECAPA 模型完成加载后返回 200，否则返回 503。

## 8076 异步任务 API

- `GET /health`、`GET /health/ready`
- `POST /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `GET /v1/tasks/{task_id}/result`
- `GET /v1/tasks/{task_id}/audio`
- `GET /v1/tasks/{task_id}/exports/{json|txt|srt|vtt}`
- `POST /v1/tasks/{task_id}/cancel`
- `DELETE /v1/tasks/{task_id}`

创建接口使用 `multipart/form-data`，`audio` 必填，`deadline_sec` 为可选正数。`input_mode=provided_transcript` 时 `segments` 必填且必须是 `voice_analysis_input_v1`；`input_mode=cloud_asr` 时禁止 `segments`，并要求 `asr_provider=tencent|aliyun`、一次性 `asr_credentials` JSON 和非敏感 `asr_options` JSON。可选 `Idempotency-Key` 长度为 1 至 200；幂等摘要不包含凭据。

云转写凭据只从请求交给已预留的进程内任务，不写入任务事实。公开任务增加 `input_mode/asr_provider/transcript_source`；阶段可包含 `transcribing/normalizing_transcript/exporting`。活动云转写任务在进程重启后以 `CREDENTIAL_LOST` 失败。

任务状态为 `queued/running/succeeded/failed/cancelled/expired`。M1 的 `success/partial` 都映射为任务 `succeeded`，原状态保存在 `result_status` 和权威结果内。非成功任务读取结果返回 409，过期返回 410；未知或已删除任务返回 404。活动任务必须先取消再删除。

取消是尽力转发：后端在 M1 阶段边界检查，不承诺中断正在执行的解码、模型请求或 CPU 计算，取消确认后的迟到结果不会发布。任务业务资产默认保留 24 小时，过期状态再保留 24 小时。

## 8078 Window Refine

- `GET http://127.0.0.1:8078/health`
- `GET http://127.0.0.1:8078/health/ready`
- `POST http://127.0.0.1:8078/segment`

初始请求沿用已核验结构：

```json
{
  "audio_path": "runtime/audio/example.wav",
  "asr_candidate_windows": [],
  "profile": {"clean_window": {}},
  "speech_db_threshold": -45.0
}
```

实现必须把 `audio_path` 解析并限制在 `VOICEANALYSIS_AUDIO_ROOT` 或 `VOICEANALYSIS_TEMPORARY_ROOT` 内，不接受其他主机路径。响应包含 `success`、`backend`、`speech_window_candidates`、逐候选 `candidate_results` 和 `audit`。

8078 对候选区域使用模型原生时长分块和重叠上下文，只随机读取当前推理区间；审计返回 `inference_ranges/model_chunk_ms/full_audio_loaded`，其中当前实现的 `full_audio_loaded` 必须为 `false`。

越界路径、不存在的文件和非法请求返回 HTTP 400。模型已经加载但具体分窗推理失败时，响应以 `success=false` 和审计错误表达；`/health/ready` 在模型未就绪时返回 503。

## 兼容边界

8077/8078 不承诺兼容 Smart Badge 已退役 sidecar。M1 编排引擎继续保留独立 CLI 契约；8076 是 M2 面向后续 Web 的统一异步入口。
