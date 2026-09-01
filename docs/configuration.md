# 配置、地址与密钥

状态：current

核验日期：2026-09-01

## 地址

| 服务 | 本地主机 | 容器网络 |
|---|---|---|
| Task API | `http://127.0.0.1:8076` | 尚未验证 |
| ECAPA health | `http://127.0.0.1:8077/health` | `http://voice-embedding:8077/health` |
| ECAPA embed | `http://127.0.0.1:8077/embed` | `http://voice-embedding:8077/embed` |
| pyannote health | `http://127.0.0.1:8078/health` | `http://window-refine:8078/health` |
| pyannote segment | `http://127.0.0.1:8078/segment` | `http://window-refine:8078/segment` |

端口来源于 Smart Badge 退役前可运行服务。新仓库没有生产域名或远程主机地址，不得自行假设。

## 密钥

| 变量 | 是否必需 | 来源与用途 |
|---|---|---|
| `VOICEANALYSIS_API_KEY` | 是 | 本地初始化时随机生成；保护 8076 任务接口、`/embed` 与 `/segment` |
| `HUGGINGFACE_ACCESS_TOKEN` | 条件必需 | 仅首次从 gated Hugging Face 仓库合法下载 pyannote 模型；离线模型齐全时为空 |

当前 Smart Badge 环境没有 `HUGGINGFACE_ACCESS_TOKEN/HF_TOKEN` 可复制来源，因此 `.env` 中该项明确为空，且默认 `WINDOW_REFINE_ALLOW_MODEL_DOWNLOAD=0`、`HF_HUB_OFFLINE=1`。需要下载时由有权限的操作者写入本地 `.env`，下载完成后恢复离线模式。

LLM、数据库、Redis、SAP、企业微信、COS/NFS 和 Smart Badge 签名密钥都不是推理进程依赖。腾讯、阿里 ASR 凭据只从本地 Web 单次提交并在活动任务内存使用，不写入 `.env`、配置、任务目录、结果或日志。

8076 与 Web 默认绑定 `127.0.0.1:8076` 且不鉴权；8077/8078 继续使用 `VOICEANALYSIS_API_KEY` 作为内部组件密钥。腾讯默认使用 Flash ASR `16k_zh`；阿里使用 NLS、默认 `cn-shanghai`，上传音频会先临时规范化为 16 kHz 单声道 PCM。

`VOICEANALYSIS_TEMPORARY_ROOT` 指定 M1 规范化 WAV 的受控临时根，默认 `runtime/tmp`。8078 允许读取音频根和临时根，但仍拒绝其他主机路径。

`VOICEANALYSIS_TASK_ROOT` 指定 M2 任务根，默认 `runtime/tasks`。`config/services.yaml` 的 `task_api` 固定首版 24 小时业务资产保留、额外 24 小时过期元数据保留、1 GiB 音频、16 MiB 句段文档、一个后端执行槽和 10 秒关闭宽限期；宽限后只取消 asyncio 包装任务，保留 `running` 状态供下次启动整任务重跑。

## M1 分析配置

`config/analysis.yaml` 是 M1 默认配置，分为两类：

- 可覆盖产品项：输入范围、组件地址、失败策略、导出展示和运行资源预算；CLI 通过 `--config <overlay.yaml>` 覆盖。
- 锁定算法项：窗口评分、黄金窗口、NME、KMeans、半径、句段归属和风险召回。覆盖文件包含 `algorithm/profile/schema_version` 或未知顶层节时直接失败。

结果记录脱敏后的有效配置、配置 SHA256、算法 Profile 和算法 SHA256。临时根记录为逻辑路径，API key 和完整主机路径不进入结果。

## 模型目录

| 模型 | 本地目录 | 来源 |
|---|---|---|
| ECAPA VoxCeleb | `runtime/models/ecapa/spkrec-ecapa-voxceleb` | `speechbrain/spkrec-ecapa-voxceleb` |
| Segmentation 3.0 | `runtime/models/pyannote/segmentation-3.0` | `pyannote/segmentation-3.0` |
| OSD（默认关闭） | `runtime/models/pyannote/overlapped-speech-detection` | `pyannote/overlapped-speech-detection` |

模型文件不进入普通 Git。当前本地权重由相邻 Smart Badge 历史资产恢复，来源提交、文件大小和 SHA256 统一记录在 `config/model-manifest.json`；`scripts/install-models-from-smart-badge.ps1` 可重复安装并校验，`scripts/verify-models.ps1` 只做完整性检查。

当前 ECAPA 和 segmentation-3.0 权重已经完成本地 SHA256 校验。旧 OSD pipeline 仍默认关闭，没有把它作为服务 ready 的前提。

## M1 基础评测

`config/evaluation.yaml` 是 M1 基础评测的版本化默认配置，包含：

- 最低录音数量、总时长和必需场景；
- 评分帧、collar、重叠计分、宏平均和 speaker 映射方法；
- DER、JER、说话人数、句段归属准确率及 `unknown` 覆盖率门槛；
- 配置摘要、数据集构成和逐指标结论的报告要求。

评测入口可以显式指定其他配置文件，但必须把最终 Profile、配置摘要和实际数据集构成写入报告。此配置只裁决验收结果，不覆盖算法资产中的权威阈值；修改默认门槛仍需进入版本管理并留下需求和评测记录。

自动报告只提供可重复的数值证据。最终质检还必须监督确认参考标签代表自然人、指标是否评价固定句段归属或完整 diarization，以及自动人数是否遗漏未用于句段归属的已建簇；用户对 M1 的成果验收不改写未达门槛的实际值。
