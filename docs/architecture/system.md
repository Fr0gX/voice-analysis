# 系统边界

状态：current design

核验日期：2026-08-27

## 1. 职责

本仓库只负责单次录音内的声音窗口细化、说话人向量和后续录音内聚类。说话人键只在本次录音内稳定，不是人员身份。

调用方负责提供音频和 ASR 时间窗口。服务不持有 Smart Badge 数据库，不调用 LLM，不请求腾讯 ASR，也不读取员工声纹库。

## 2. 进程

```text
caller
  ├─ POST 8078 /segment → pyannote segmentation → 单人候选窗口
  └─ POST 8077 /embed   → SpeechBrain ECAPA → 192维归一化向量

候选窗口 + 向量
  → 单录音内余弦聚类（后续代码任务）
  → local speaker cluster / unknown
```

8077 和 8078 使用不同 Python 环境、不同模型目录、不同端口和独立健康检查。任何一方失败都必须显式返回错误；不得用 ASR speaker 标签伪装本地聚类成功。

## 3. 数据边界

- 输入音频只落在 `runtime/audio`，临时产物只落在 `runtime/tmp`，均不进入 Git。
- 默认不持久化 embedding；日志不得打印音频正文、向量或访问密钥。
- 模型权重位于 `runtime/models`，由合法离线来源或显式授权下载提供。
- 不建立跨录音身份、员工匹配、自动学习或声纹回灌。

## 4. 已核验技术基线

- 16 kHz、单声道 PCM。
- pyannote.audio 4.x 与 `pyannote/segmentation-3.0` 负责窗口细化。
- SpeechBrain ECAPA-VoxCeleb 输出 192 维 L2 归一化向量。
- 常规音频由 miniaudio 解码，FFmpeg 作为格式兼容工具。
- 聚类只能读取窗口向量，不能用 ASR `speaker_id` 决定簇。
