# 系统边界

状态：current implementation

核验日期：2026-08-31

## 1. 职责

本仓库当前提供单次录音内声音窗口细化和说话人向量两个底层模型服务。说话人键只允许在本次录音内稳定，不是人员身份。

调用方负责提供音频和 ASR 时间窗口。服务不持有 Smart Badge 数据库，不调用 LLM，不请求腾讯 ASR，也不读取员工声纹库。

## 2. 进程

```text
caller
  ├─ POST 8078 /segment → pyannote segmentation → 单人候选窗口
  └─ POST 8077 /embed   → SpeechBrain ECAPA → 192维归一化向量

候选窗口 + 向量
  → 单录音内聚类与句段归属（尚未实现）
  → local speaker cluster / unknown
```

8077 和 8078 使用不同 Python 环境、不同模型目录、不同端口和独立健康检查。任何一方失败都必须显式返回错误；不得用 ASR speaker 标签伪装本地聚类成功。

当前代码入口：

- `voice_embedding_service.app:create_app`：8077 `/health`、`/health/ready`、`/embed`；
- `window_refine_service.app:create_app`：8078 `/health`、`/health/ready`、`/segment`；
- `scripts/start-local-services.ps1`：本机联合启动、等待真实模型 ready 并记录 PID；
- `scripts/stop-local-services.ps1`：只停止联合启动脚本记录的两个本机进程。

统一的上传、异步任务、聚类、完整转写和 Web 页面尚不存在，不属于当前已实现能力。

## 3. 数据边界

- 输入音频只落在 `runtime/audio`，临时产物只落在 `runtime/tmp`，均不进入 Git。
- 默认不持久化 embedding；日志不得打印音频正文、向量或访问密钥。
- 模型权重位于 `runtime/models`，由合法离线来源提供；`config/model-manifest.json` 记录来源、大小和 SHA256，权重不进入普通 Git。
- 不建立跨录音身份、员工匹配、自动学习或声纹回灌。
- `/embed` 和 `/segment` 使用独立 API key；`/segment` 只允许读取 `VOICEANALYSIS_AUDIO_ROOT` 内的文件。

## 4. 已核验技术基线

- 16 kHz、单声道 PCM。
- pyannote.audio 4.x 与 `pyannote/segmentation-3.0` 负责窗口细化。
- SpeechBrain ECAPA-VoxCeleb 输出 192 维 L2 归一化向量。
- 常规音频由 miniaudio 解码，FFmpeg 作为格式兼容工具。
- 未来聚类只能读取窗口向量，不能用 ASR `speaker_id` 决定簇。
