# Voice Analysis

独立声音分离服务工作区。当前阶段已经准备文档、配置、密钥、模型目录和两套隔离 Python 环境；尚未实现 HTTP 服务代码，也未接入 Smart Badge。

核心边界：

```text
调用方音频 + ASR 时间窗口
  → 8078 pyannote 窗口细化
  → 8077 ECAPA 192 维向量
  → 单录音内余弦聚类（待实现）
  → 片段说话人簇或 unknown
```

- 文档入口：[docs/README.md](docs/README.md)
- 无密配置模板：[.env.example](.env.example)
- 服务参数：[config/services.yaml](config/services.yaml)
- Python 环境：[environments/README.md](environments/README.md)
- 本地初始化：[docs/operations/local-development.md](docs/operations/local-development.md)

本仓库不保存真实录音、模型权重、embedding、身份库或明文密钥。
