# Voice Analysis

Voice Analysis 支持直接使用已有 ASR 时间轴，或用用户单次提供的腾讯／阿里凭据先完成云转写，再生成单次录音内的说话人分离结果。

当前仓库已经具备 M1 分析引擎、M2 任务 API、M3 本地 Web 与云 ASR provider，以及两个底层模型服务：

- `voice_analysis_engine`：校验音频和 ASR 句段，编排模型、聚类、归属并导出结果；
- `8076`：安全上传、任务轮询、结果下载、取消、删除和 24 小时清理；
- `web`：双入口提交、临时 ASR 配置、任务状态、同步播放、复核与导出；
- `8078`：使用 pyannote segmentation-3.0 细化语音窗口；
- `8077`：使用 SpeechBrain ECAPA-VoxCeleb 生成 192 维说话人向量。

M1 工程成果已经用户验收；自然人数估计、聚类混淆、置信度校准和短句归属仍作为后续算法优化项，验收不表示冷启动精度门槛已经全部通过。

首版只监听本机且没有用户或业务接口鉴权；ASR 凭据仅存在于活动任务内存，服务重启会使自动转写任务以 `CREDENTIAL_LOST` 失败。系统仍为单进程、单后端执行槽，不提供数据库、多节点或实时推送。说话人标签只在单次录音内有效。

## 输入与目标输出

输入：原始录音及标准 ASR JSON，或原始录音及单次腾讯／阿里 ASR 配置。

输出：权威 JSON 以及从它派生的 TXT、SRT、VTT；保留原始文字和时间戳，并为每个句段增加本地说话人标签、置信度或 `unknown` 状态。

## 本地启动

```powershell
./scripts/initialize-local-env.ps1
./scripts/bootstrap-python.ps1 -InstallDependencies
./scripts/install-models-from-smart-badge.ps1
./scripts/verify-workspace.ps1
Push-Location web; npm install; npm run build; Pop-Location
./scripts/start-all-services.ps1
```

本地服务：

- Task API：`http://127.0.0.1:8076`
- Web：`http://127.0.0.1:8076/`
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

一键脚本会先执行工作区检查和 Web 生产构建，再启动 8077、8078，等待真实模型 ready 后启动 8076。标准输出会打印访问地址；PID 位于 `runtime/tmp/local-services.json`，日志位于 `runtime/logs/`。完整说明与故障处理见 [本地环境手册](docs/operations/local-development.md#一键启动全套服务)。

## 项目入口

- [项目现状、资产与后续需求](PROJECT_STATUS.md)
- [项目文档入口](docs/README.md)
- [系统边界](docs/architecture/system.md)
- [HTTP 接口](docs/contracts/http-api.md)
- [M1 分析引擎契约](docs/contracts/analysis-engine.md)
- [配置说明](docs/configuration.md)
- [本地开发与测试](docs/operations/local-development.md)

模型权重、真实录音、说话人向量和本地密钥不进入 Git。
