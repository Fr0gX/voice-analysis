# 本地环境初始化

状态：current

核验日期：2026-09-01

## 前置

- Windows PowerShell 7
- `uv`
- Git 与 Git LFS
- FFmpeg
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

## 启动与停止

分别以前台方式启动：

```powershell
./scripts/start-voice-embedding.ps1
./scripts/start-window-refine.ps1
```

联合后台启动、等待真实模型 ready、执行探针并停止：

```powershell
./scripts/start-local-services.ps1
./scripts/smoke-services.ps1
./scripts/stop-local-services.ps1
```

联合启动器只记录并管理本次创建的两个 PID，日志写入 `runtime/logs`。不要用该脚本停止其他端口或其他项目进程。

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
