# HTTP 契约基线

状态：实现前契约

核验日期：2026-08-27

除 `/health` 外，请求必须携带：

```http
X-Voice-Analysis-Key: <VOICEANALYSIS_API_KEY>
```

## 8077 Voice Embedding

- `GET http://127.0.0.1:8077/health`
- `POST http://127.0.0.1:8077/embed`

`/embed` 使用 `multipart/form-data`：`metadata` 为任务、窗口、PCM 偏移和截止时间 JSON；`audio` 为 16 kHz 单声道 signed int16 little-endian PCM。成功项返回 192 维归一化向量，逐项状态为 `success/too_short/invalid_boundary/inference_failed`。

协议错误、请求过大、队列过载、模型未就绪和超时分别使用 HTTP 400、413、429、503、504。

## 8078 Window Refine

- `GET http://127.0.0.1:8078/health`
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

实现必须把 `audio_path` 解析并限制在 `VOICEANALYSIS_AUDIO_ROOT` 内，不接受任意主机绝对路径。响应包含 `success`、`backend`、`speech_window_candidates` 和 `audit`。

## 兼容边界

这是新独立服务的实现基线，不承诺兼容 Smart Badge 已退役 sidecar。接口实现、Schema 和错误码变化必须先更新需求和本契约。
