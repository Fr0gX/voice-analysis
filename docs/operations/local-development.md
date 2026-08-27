# 本地环境初始化

状态：current

核验日期：2026-08-27

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

默认离线运行。将有明确来源和许可的模型文件放入 `docs/configuration.md` 指定目录。只有明确取得 Hugging Face gated 模型权限时，才临时填写 token 并开启下载；不要把 token、模型或缓存提交到 Git。

## 验证

```powershell
./scripts/verify-workspace.ps1
```

未实现服务代码前，验证只检查目录、配置、秘密存在性、Python 版本、FFmpeg 和模型就绪情况。模型缺失会报告 `not-ready`，不会被当成已可启动。

## 启动边界

当前没有服务实现入口，因此不要占用 8077/8078 或建立虚假健康响应。代码实现后应分别由两个环境启动，并在服务实际加载模型后再让 `/health` 返回 ready。
