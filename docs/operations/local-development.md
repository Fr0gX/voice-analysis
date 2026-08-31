# 本地环境初始化

状态：current

核验日期：2026-08-31

## 前置

- Windows PowerShell 7
- `uv`
- Git 与 Git LFS
- FFmpeg
- 可用磁盘空间：两个 Python 环境和模型至少预留 15 GiB

## 初始化秘密

```powershell
./scripts/initialize-local-env.ps1
```

脚本从 `.env.example` 生成被 Git 忽略的 `.env`，并写入随机 256-bit URL-safe API key。已有 `.env` 时不覆盖，除非显式使用 `-Force`。

## 创建环境

```powershell
./scripts/bootstrap-python.ps1 -InstallDependencies
```

该脚本安装/定位 Python 3.11，创建 `.venv-embedding` 与 `.venv-refine`，再分别安装依赖。它不会下载模型。

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
