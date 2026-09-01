# 系统边界

状态：current implementation

核验日期：2026-09-01

## 1. 职责

本仓库当前提供已经用户验收的 M1 单录音分析引擎，以及声音窗口细化和说话人向量两个底层模型服务。说话人键表示本次录音内区分出的自然人，只允许在本次录音内稳定，不是可用于跨录音匹配的人员身份。

调用方可以提供音频和 ASR 时间窗口，也可以单次提供腾讯或阿里 ASR 凭据由系统先转写。服务不持有 Smart Badge 数据库，不调用 LLM，不持久化云凭据，也不读取员工声纹库。

## 2. 进程

```text
CLI
  → voice_analysis_engine（异步阶段编排）
      → 受控临时 WAV + 随机区间读取
      → POST 8078 /segment → pyannote 分块细化
      → 窗口评分和黄金窗口
      → POST 8077 /embed → 有界批量 ECAPA
      → NME + 加权 KMeans + speaker 代表
      → 完整 ASR 句段归属或 unknown
      → result.json → TXT/SRT/VTT

HTTP caller
  → React Web（双入口、临时配置、同步复核与导出）
  → 8076 voice_analysis_api（任务事实、上传、轮询和清理）
      → Tencent Flash ASR 或 Aliyun NLS（可选、凭据仅活动任务内存）
      → 进程内 LocalAnalysisBackend（单执行槽、无新任务等待队列）
          → voice_analysis_engine
```

8077 和 8078 使用不同 Python 环境、不同模型目录、不同端口和独立健康检查。任何一方失败都必须显式返回错误；不得用 ASR speaker 标签伪装本地聚类成功。

分析引擎和 8076 使用第三个不包含 Torch 的轻量环境。M2 通过可替换的进程内后端原子准入；当前后端单执行槽且不为新请求排队，忙时直接返回 429。每个任务仍由 M1 `StageExecutor` 按阶段执行。

当前代码入口：

- `voice_embedding_service.app:create_app`：8077 `/health`、`/health/ready`、`/embed`；
- `window_refine_service.app:create_app`：8078 `/health`、`/health/ready`、`/segment`；
- `voice_analysis_engine.AnalysisEngine`：M1 单录音处理入口；
- `voice_analysis_api.app:create_app`：M2 任务 HTTP API；
- `python -m voice_analysis_engine`：分析和评测 CLI；
- `scripts/start-local-services.ps1`：本机联合启动、等待真实模型 ready 并记录 PID；
- `scripts/stop-local-services.ps1`：只停止联合启动脚本记录的两个本机进程。

统一上传、双入口 Web、云转写、异步轮询、取消、删除和 24 小时保留已经实现。已有转写任务重启整任务重跑；云转写因凭据不落盘而明确失败。数据库、多进程抢占、多节点、断点续跑和实时推送尚不存在。

## 3. 数据边界

- CLI 输入音频由调用方指定；M2 上传写入 `runtime/tasks/<task_id>/input`。规范化 WAV 只落在 `runtime/tmp/<run_id>`，正常结束后删除；M2 结果位于任务目录并按保留策略清理。运行目录均不进入 Git。
- 默认不持久化 embedding；日志不得打印音频正文、向量或访问密钥。
- 模型权重位于 `runtime/models`，由合法离线来源提供；`config/model-manifest.json` 记录来源、大小和 SHA256，权重不进入普通 Git。
- 不建立跨录音身份、员工匹配、自动学习或声纹回灌。
- `/embed` 和 `/segment` 使用同一仓库级 API key；`/segment` 只允许读取音频根或临时根内的文件。
- 句段、窗口描述、embedding 和聚类状态在当前运行内存中存在；完整 PCM 通过磁盘文件随机读取，崩溃后不恢复中间状态。

## 4. 已核验技术基线

- 16 kHz、单声道 PCM。
- pyannote.audio 4.x 与 `pyannote/segmentation-3.0` 负责窗口细化。
- SpeechBrain ECAPA-VoxCeleb 输出 192 维 L2 归一化向量。
- 常规音频由 miniaudio 解码，FFmpeg 作为格式兼容工具。
- 当前聚类只读取窗口向量；ASR `speaker` 只保存和回显，不参与候选区、人数估计、聚类、归属或 unknown 填充。

## 5. M1 当前验收与质量事实

- M1 的单录音处理闭环、组件兼容、阶段资源边界、临时数据清理、确定性、结果契约、失败表达、真实模型运行和评测能力已经用户验收；M2 可以直接复用 `AnalysisEngine` 和 `StageExecutor` 契约继续开发。
- 用户确认的 12 份最终审核转写把不同标签准确分配给单份录音内的不同自然人。标签使用何种角色文字不影响评分，评测只把它们当作可置换的自然人编号。
- 当前结果附着在调用方提供的 ASR 句段上，监督评测衡量固定句段的自然人归属和拒识，不包含独立语音活动检测及精确说话人边界能力。
- 12 份、8.241 小时监督评测中，12/12 引擎运行成功；固定句段自然人错误率为 0.492006，已归属句段准确率为 0.510550。按全部实际建簇复核的人数完全正确率为 0.333333、人数 MAE 为 0.916667。
- 当前主要算法风险为自然人合并和拆分同时存在、归属置信等级未校准，以及不足 1.5 秒的句段全部拒识。用户接受这些风险并验收 M1，但没有把未达到的 `m1_baseline_v1` 数值门槛改写为通过。
- 自动评测器是计算工具，不是最终验收主体。当前人数观察值只计算至少用于一个最终句段的标签，监督质检还必须核对引擎实际建立的全部簇。
