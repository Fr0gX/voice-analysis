# 配置、地址与密钥

状态：current

核验日期：2026-08-27

## 地址

| 服务 | 本地主机 | 容器网络 |
|---|---|---|
| ECAPA health | `http://127.0.0.1:8077/health` | `http://voice-embedding:8077/health` |
| ECAPA embed | `http://127.0.0.1:8077/embed` | `http://voice-embedding:8077/embed` |
| pyannote health | `http://127.0.0.1:8078/health` | `http://window-refine:8078/health` |
| pyannote segment | `http://127.0.0.1:8078/segment` | `http://window-refine:8078/segment` |

端口来源于 Smart Badge 退役前可运行服务。新仓库没有生产域名或远程主机地址，不得自行假设。

## 密钥

| 变量 | 是否必需 | 来源与用途 |
|---|---|---|
| `VOICEANALYSIS_API_KEY` | 是 | 本地初始化时随机生成；保护 `/embed` 与 `/segment` |
| `HUGGINGFACE_ACCESS_TOKEN` | 条件必需 | 仅首次从 gated Hugging Face 仓库合法下载 pyannote 模型；离线模型齐全时为空 |

当前 Smart Badge 环境没有 `HUGGINGFACE_ACCESS_TOKEN/HF_TOKEN` 可复制来源，因此 `.env` 中该项明确为空，且默认 `WINDOW_REFINE_ALLOW_MODEL_DOWNLOAD=0`、`HF_HUB_OFFLINE=1`。需要下载时由有权限的操作者写入本地 `.env`，下载完成后恢复离线模式。

LLM、腾讯 ASR、数据库、Redis、SAP、企业微信、COS/NFS 和 Smart Badge 签名密钥都不是这两个推理进程的依赖，不复制到本仓库。

## 模型目录

| 模型 | 本地目录 | 来源 |
|---|---|---|
| ECAPA VoxCeleb | `runtime/models/ecapa/spkrec-ecapa-voxceleb` | `speechbrain/spkrec-ecapa-voxceleb` |
| Segmentation 3.0 | `runtime/models/pyannote/segmentation-3.0` | `pyannote/segmentation-3.0` |
| OSD（默认关闭） | `runtime/models/pyannote/overlapped-speech-detection` | `pyannote/overlapped-speech-detection` |

模型文件不进入普通 Git。若未来使用 Git LFS，必须先记录来源许可、SHA256 和模型版本，不能只提交匿名大文件。
