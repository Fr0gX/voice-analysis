# Voice Analysis

Voice Analysis 用于根据原始录音和已有的 ASR 文字时间轴，生成单次录音内的说话人分离结果。

当前仓库已经具备 M1 单录音分析引擎和两个可独立运行的底层模型服务：

- `voice_analysis_engine`：校验音频和 ASR 句段，编排模型、聚类、归属并导出结果；
- `8078`：使用 pyannote segmentation-3.0 细化语音窗口；
- `8077`：使用 SpeechBrain ECAPA-VoxCeleb 生成 192 维说话人向量。

异步任务 API、统一上传和 Web 页面尚未实现。说话人标签只在单次录音内有效，不表示人员身份，也不用于跨录音匹配。

## 输入与目标输出

输入：原始录音，以及包含文字、开始时间和结束时间的 ASR 句段。

输出：权威 JSON 以及从它派生的 TXT、SRT、VTT；保留原始文字和时间戳，并为每个句段增加本地说话人标签、置信度或 `unknown` 状态。

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

服务 ready 后分析一份录音：

```powershell
.venv-analysis/Scripts/python.exe -m voice_analysis_engine analyze `
  --audio runtime/audio/example.wav `
  --segments runtime/audio/example-segments.json `
  --output runtime/audio/example-result
```

句段文件的顶层为 `voice_analysis_input_v1`，完整字段见 [M1 分析引擎契约](docs/contracts/analysis-engine.md)。

相邻 Smart Badge 工作区包含“录音2”资产时，可运行 `./scripts/prepare-recording2-evaluation.ps1` 构建 20 个可追溯弱标注样本；它只用于真实录音基线，不作为人工身份真值。

停止服务：

```powershell
./scripts/stop-local-services.ps1
```

## 项目入口

- [项目现状、资产与后续需求](PROJECT_STATUS.md)
- [项目文档入口](docs/README.md)
- [系统边界](docs/architecture/system.md)
- [HTTP 接口](docs/contracts/http-api.md)
- [M1 分析引擎契约](docs/contracts/analysis-engine.md)
- [配置说明](docs/configuration.md)
- [本地开发与测试](docs/operations/local-development.md)

模型权重、真实录音、说话人向量和本地密钥不进入 Git。
