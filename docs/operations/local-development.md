# 本地环境初始化

状态：current

核验日期：2026-09-01

## 前置

- Windows PowerShell 7
- `uv`
- Git 与 Git LFS
- FFmpeg
- Node.js 与 npm
- 可用磁盘空间：三个 Python 环境、模型及长音频临时 WAV 至少预留 16 GiB

## 初始化秘密

```powershell
./scripts/initialize-local-env.ps1
```

脚本从 `.env.example` 生成被 Git 忽略的 `.env`，并写入随机 256-bit URL-safe API key。已有 `.env` 时不覆盖，除非显式使用 `-Force`。

## 创建环境

```powershell
./scripts/bootstrap-python.ps1 -InstallDependencies
```

该脚本安装/定位 Python 3.11，创建 `.venv-embedding`、`.venv-refine` 与 `.venv-analysis`，再分别安装锁定依赖。分析环境不加载 Torch；脚本不会下载模型。

M3 Web 首次运行还需要安装、测试并构建前端：

```powershell
Push-Location web
npm install
npm test
npm run build
Pop-Location
```

生产构建由 8076 同源托管在 `http://127.0.0.1:8076/`。开发服务器只监听 `127.0.0.1:5173` 并把 `/v1`、`/health` 代理到 8076。

## 模型

默认离线运行。当前相邻 Smart Badge 历史资产可用时，从仓库根目录执行：

```powershell
./scripts/install-models-from-smart-badge.ps1
./scripts/verify-models.ps1
```

脚本只把清单内文件复制到被 Git 忽略的 `runtime/models`，并校验大小和 SHA256。只有明确取得 Hugging Face gated 模型权限时，才临时填写 token 并开启下载；不要把 token、模型或缓存提交到 Git。

## 验证

```powershell
./scripts/verify-workspace.ps1
```

验证检查目录、配置、秘密、Python 版本、FFmpeg、服务源码和模型完整性。模型缺失会报告 `not-ready`，不会被当成已可启动。

## 一键启动全套服务

完成首次环境、前端依赖和模型安装后，日常启动只需：

```powershell
./scripts/start-all-services.ps1
```

脚本按固定顺序执行：

1. `verify-workspace.ps1` 检查三套 Python、Node/npm、FFmpeg、配置、密钥和模型；
2. 使用已安装的 npm 依赖构建 `web/dist`；
3. 后台启动 8077 ECAPA 和 8078 pyannote，等待两个真实模型 `/health/ready`；
4. 后台启动 8076 Web/Task API，并等待聚合 `/health/ready`；
5. 打印页面、健康检查、日志和停止命令。

模型首次加载较慢时可调整等待时间：

```powershell
./scripts/start-all-services.ps1 -ReadyTimeoutSec 600
```

已经单独执行过工作区检查时可以使用 `-SkipWorkspaceVerification`，但脚本仍会验证模型、构建 Web 并等待各服务 ready。页面地址为 `http://127.0.0.1:8076/`；8076 只监听本机。

脚本只记录和管理本次创建的三个进程，PID 文件为 `runtime/tmp/local-services.json`。重复启动且 PID 文件仍存在时会拒绝执行，先核对状态并使用停止脚本：

```powershell
./scripts/stop-local-services.ps1
```

日志分别位于：

- `runtime/logs/task-api.stdout.log` 与 `task-api.stderr.log`
- `runtime/logs/voice-embedding.stdout.log` 与 `voice-embedding.stderr.log`
- `runtime/logs/window-refine.stdout.log` 与 `window-refine.stderr.log`

启动失败时联合启动器会停止本次已创建的进程并删除 PID 文件。常见原因依次检查：`npm install` 是否完成、模型文件摘要是否通过、`.env` 是否存在、8076/8077/8078 是否被其他进程占用，以及对应 stderr 日志。脚本不会停止未由它创建的其他项目进程。

## 分组件启动与停止

分别以前台方式启动：

```powershell
./scripts/start-voice-embedding.ps1
./scripts/start-window-refine.ps1
```

底层联合启动脚本、执行探针并停止：

```powershell
./scripts/start-local-services.ps1
./scripts/smoke-services.ps1
./scripts/stop-local-services.ps1
```

`start-all-services.ps1` 是日常使用入口；`start-local-services.ps1` 是它调用的底层联合启动器。只以前台方式启动任务 API 可执行：

```powershell
./scripts/start-task-api.ps1
```

提交任务使用 multipart `audio` 和 `segments`，随后轮询 `/v1/tasks/{task_id}`；完整字段和状态语义见 HTTP 契约。任务资产位于 Git 忽略的 `runtime/tasks`，默认 24 小时后过期。

## 测试

```powershell
./scripts/test-services.ps1
```

ECAPA 私有 parity 语料没有迁入，对应测试会跳过。组件测试和合成波形探针不能替代真实业务录音准确率评测。

## M1 分析与评测

先启动 8077/8078，再执行：

```powershell
.venv-analysis/Scripts/python.exe -m voice_analysis_engine analyze `
  --audio runtime/audio/example.wav `
  --segments runtime/audio/example-segments.json `
  --output runtime/audio/example-result
```

正常结果包含 `result.json/transcript.txt/transcript.srt/transcript.vtt`。退出码 `2` 表示存在局部模型失败但权威部分结果已写出；系统性失败只生成 `failure.json`。

评测 Manifest 和参考标注格式见分析引擎契约。执行：

```powershell
.venv-analysis/Scripts/python.exe -m voice_analysis_engine evaluate `
  --manifest <dataset.jsonl> `
  --report <report.json>
```

评测完成后不能只读取报告状态机械验收。监督复核至少包括：

1. 确认参考标签在单份录音内代表不同自然人；标签名称中的业务角色文字不参与裁决。
2. 确认当前 DER/JER 只评价固定转写句段的自然人归属和拒识，不解释为独立语音活动检测或精确说话人边界结果。
3. 同时检查结果中的全部实际建簇数和最终使用标签数；当前自动人数指标只使用后者，可能掩盖未分配到句段的多余簇。
4. 联合解释自然人混淆、拒识、已归属准确率和覆盖率；非 `unknown` 覆盖率与 `unknown` 比例互为补数，不当作两份独立证据。
5. 将数据资格、数值门槛和用户里程碑验收分开记录。用户接受已知风险可以使成果验收通过，但不得修改实际指标或伪写门槛通过。

M1 不恢复崩溃任务；异常遗留的 `runtime/tmp/<run_id>` 可以在确认没有分析进程使用后人工清理。最终输出目录不应放在 `runtime/tmp` 下。

## “录音2”弱标注评测集

相邻 Smart Badge 工作区存在 `refer/录音2` 时，可从仓库根目录执行：

```powershell
./scripts/prepare-recording2-evaluation.ps1
```

脚本不修改来源文件，会把 12 个独立源录音按转写边界派生为 20 个互不重叠样本，并在被 Git 忽略的 `runtime/evaluation/recording2_weak_v1` 写入音频、`voice_analysis_input_v1`、参考标注、Manifest、数据集元数据和验证报告。目标已存在时只有显式传入 `-Force` 才会重建。

来源角色和 speaker 是内部 ASR 弱标注，不是人工身份真值。构建器会修复零时长段、统一重复 overlap 后缀、保留原标签和修复标记，并把派生样本数与独立源录音数分别记录。运行真实评测：

```powershell
./scripts/start-local-services.ps1
.venv-analysis/Scripts/python.exe -m voice_analysis_engine evaluate `
  --manifest runtime/evaluation/recording2_weak_v1/manifest.jsonl `
  --report runtime/evaluation/recording2_weak_v1/evaluation-report.json
./scripts/stop-local-services.ps1
```

该数据集预期返回 `insufficient_dataset`；这是独立录音数和弱标注资格结论，不等同于组件运行失败。逐样本状态、观察指标和逐门槛比较仍写入报告。
