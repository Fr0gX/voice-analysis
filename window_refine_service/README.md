# Window Refine Service

该进程使用 pyannote.audio 4.x 和 segmentation-3.0，在调用方提供的 ASR 候选时间范围内细化连续单人语音窗口，并标记重叠、说话人变化和质量信号。

它不提取说话人向量，不聚类，也不识别人员身份。

## 地址

- `GET http://127.0.0.1:8078/health`
- `GET http://127.0.0.1:8078/health/ready`
- `POST http://127.0.0.1:8078/segment`

`/segment` 必须携带 `X-Voice-Analysis-Key`。`audio_path` 必须解析到 `VOICEANALYSIS_AUDIO_ROOT` 内，越界路径和不存在的文件会被拒绝。请求契约见 [`../docs/contracts/http-api.md`](../docs/contracts/http-api.md)。

## 模型

默认模型目录：

```text
runtime/models/pyannote/segmentation-3.0
```

模型来源、文件大小和 SHA256 以 [`../config/model-manifest.json`](../config/model-manifest.json) 为准。旧 OSD pipeline 默认关闭；主链使用 segmentation-3.0 powerset 输出判断重叠。

## 启动与测试

从仓库根目录运行：

```powershell
./scripts/start-window-refine.ps1
./scripts/test-services.ps1
```
