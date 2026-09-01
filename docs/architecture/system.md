# 系统边界

状态：current implementation

核验日期：2026-09-01

## 1. 职责

本仓库当前提供 M1 单录音分析引擎，以及声音窗口细化和说话人向量两个底层模型服务。说话人键只允许在本次录音内稳定，不是人员身份。

调用方负责提供音频和 ASR 时间窗口。服务不持有 Smart Badge 数据库，不调用 LLM，不请求腾讯 ASR，也不读取员工声纹库。

## 2. 进程

```text
CLI / future M2 caller
  → voice_analysis_engine（异步阶段编排）
      → 受控临时 WAV + 随机区间读取
      → POST 8078 /segment → pyannote 分块细化
      → 窗口评分和黄金窗口
      → POST 8077 /embed → 有界批量 ECAPA
      → NME + 加权 KMeans + speaker 代表
      → 完整 ASR 句段归属或 unknown
      → result.json → TXT/SRT/VTT
```

8077 和 8078 使用不同 Python 环境、不同模型目录、不同端口和独立健康检查。任何一方失败都必须显式返回错误；不得用 ASR speaker 标签伪装本地聚类成功。

分析引擎使用第三个不包含 Torch 的轻量环境。每个阶段通过 `StageExecutor` 声明音频 IO、8078、8077、CPU 聚类或导出 IO 资源；M1 内联执行单任务，M2 可以替换为全局调度器。等待模型阶段时不持有其他资源槽位。

当前代码入口：

- `voice_embedding_service.app:create_app`：8077 `/health`、`/health/ready`、`/embed`；
- `window_refine_service.app:create_app`：8078 `/health`、`/health/ready`、`/segment`；
- `voice_analysis_engine.AnalysisEngine`：M1 单录音处理入口；
- `python -m voice_analysis_engine`：分析和评测 CLI；
- `scripts/start-local-services.ps1`：本机联合启动、等待真实模型 ready 并记录 PID；
- `scripts/stop-local-services.ps1`：只停止联合启动脚本记录的两个本机进程。

统一上传、异步任务 API、跨任务 Worker 调度、任务恢复和 Web 页面尚不存在；当前通过 CLI 验证 M1 算法闭环。

## 3. 数据边界

- 输入音频由调用方指定；规范化 WAV 只落在 `runtime/tmp/<run_id>`，正常结束后删除。最终结果写入调用方输出目录；运行目录均不进入 Git。
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
