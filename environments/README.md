# Python 环境

两个推理进程和轻量分析引擎使用隔离环境，避免编排进程加载 Torch，并避免 SpeechBrain ECAPA 与 pyannote.audio 的依赖互相牵制。

| 环境 | 目录 | Python | 依赖入口 | 职责 |
|---|---|---:|---|---|
| voice embedding | `.venv-embedding` | 3.11 | `voice-embedding/requirements.txt` | ECAPA 192 维向量，8077 |
| window refine | `.venv-refine` | 3.11 | `window-refine/requirements.txt` | pyannote 窗口细化，8078 |
| analysis engine | `.venv-analysis` | 3.11 | `analysis-engine/requirements.txt` | M1 编排、聚类、导出和评测 CLI |

运行 `scripts/bootstrap-python.ps1 -InstallDependencies` 会用 `uv` 安装 Python 3.11、创建三个环境并按各自的 `requirements.lock.txt` 安装依赖；`requirements.txt` 保存直接依赖约束，锁文件保存本次完整解析结果。两个模型环境当前均使用可从软件源成对解析的 Torch/Torchaudio 2.11；分析环境只包含 HTTP、Schema、数值计算和测试依赖。模型权重不由 Python 包安装过程下载。

FFmpeg 是系统依赖；Windows 当前可从 `PATH` 解析。Linux/Docker 环境需要安装 `ffmpeg` 和基础音频库。
