# Voice Embedding Service

该进程加载 SpeechBrain ECAPA-VoxCeleb，接收一批 16 kHz 单声道 signed int16 little-endian PCM 窗口，返回逐窗口的 192 维 L2 归一化说话人向量。

它只负责向量推理，不负责 ASR、聚类、人员身份或跨录音匹配。

## 地址

- `GET http://127.0.0.1:8077/health`
- `GET http://127.0.0.1:8077/health/ready`
- `POST http://127.0.0.1:8077/embed`

`/embed` 必须携带 `X-Voice-Analysis-Key`。请求契约见 [`../docs/contracts/http-api.md`](../docs/contracts/http-api.md)。

## 模型

默认模型目录：

```text
runtime/models/ecapa/spkrec-ecapa-voxceleb
```

模型来源、文件大小和 SHA256 以 [`../config/model-manifest.json`](../config/model-manifest.json) 为准。服务不在线下载模型，也不提供替代 embedding 算法。

## 启动与测试

从仓库根目录运行：

```powershell
./scripts/start-voice-embedding.ps1
./scripts/test-services.ps1
```

模型、队列或协议失败会通过 HTTP 状态或逐项状态明确返回；不会用零向量表示成功。
