# HTTP 契约基线

状态：当前组件契约

核验日期：2026-09-01

除 `/health` 外，请求必须携带：

```http
X-Voice-Analysis-Key: <VOICEANALYSIS_API_KEY>
```

`/health/ready` 同样不需要鉴权。业务接口缺少或提供错误 key 时返回 HTTP 401。

## 8077 Voice Embedding

- `GET http://127.0.0.1:8077/health`
- `GET http://127.0.0.1:8077/health/ready`
- `POST http://127.0.0.1:8077/embed`

`/embed` 使用 `multipart/form-data`：`metadata` 为任务、窗口、PCM 偏移和截止时间 JSON；`audio` 为 16 kHz 单声道 signed int16 little-endian PCM。成功项返回 192 维归一化向量，逐项状态为 `success/too_short/invalid_boundary/inference_failed`。

协议错误、请求过大、队列过载、模型未就绪和超时分别使用 HTTP 400、413、429、503、504。

`/health` 始终返回组件状态；`/health/ready` 只有在真实 ECAPA 模型完成加载后返回 200，否则返回 503。

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

这是新独立服务的当前组件契约，不承诺兼容 Smart Badge 已退役 sidecar。M1 编排引擎使用独立 CLI 契约；统一上传和异步任务 HTTP API 尚未定义。
