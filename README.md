# Voice Analysis

Voice Analysis 用于根据原始录音和已有的 ASR 文字时间轴，生成单次录音内的说话人分离结果。

当前仓库已经具备两个可独立运行的底层模型服务：

- `8078`：使用 pyannote segmentation-3.0 细化语音窗口；
- `8077`：使用 SpeechBrain ECAPA-VoxCeleb 生成 192 维说话人向量。

统一上传、录音内聚类、完整转写输出和 Web 页面尚未实现。说话人标签只在单次录音内有效，不表示人员身份，也不用于跨录音匹配。

## 输入与目标输出

输入：原始录音，以及包含文字、开始时间和结束时间的 ASR 句段。

目标输出：保留原始文字和时间戳，并为每个句段增加本地说话人标签、置信度或 `unknown` 状态。

## 本地启动

```powershell
./scripts/initialize-local-env.ps1
./scripts/bootstrap-python.ps1 -InstallDependencies
./scripts/install-models-from-smart-badge.ps1
./scripts/verify-workspace.ps1
./scripts/start-local-services.ps1
```

本地服务：

- Voice Embedding：`http://127.0.0.1:8077`
- Window Refine：`http://127.0.0.1:8078`

停止服务：

```powershell
./scripts/stop-local-services.ps1
```

## 项目入口

- [项目现状、资产与后续需求](PROJECT_STATUS.md)
- [项目文档入口](docs/README.md)
- [系统边界](docs/architecture/system.md)
- [HTTP 接口](docs/contracts/http-api.md)
- [配置说明](docs/configuration.md)
- [本地开发与测试](docs/operations/local-development.md)

模型权重、真实录音、说话人向量和本地密钥不进入 Git。
