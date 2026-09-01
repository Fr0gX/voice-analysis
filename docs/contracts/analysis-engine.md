# M1 分析引擎契约

状态：current

核验日期：2026-09-01

## CLI

```powershell
python -m voice_analysis_engine analyze --audio <path> --segments <json> --output <dir> [--config <overlay.yaml>] [--deadline-sec <n>]
python -m voice_analysis_engine evaluate --manifest <jsonl> --report <json> [--config config/evaluation.yaml]
```

分析退出码为：`0` 完整成功、`2` 部分结果、`10` 输入或配置错误、`20` 系统性组件错误、`21` 截止时间、`30` 内部错误。失败时只写脱敏的 `failure.json`。

## 输入

```json
{
  "schema_version": "voice_analysis_input_v1",
  "segments": [
    {
      "id": "seg_001",
      "start_ms": 1200,
      "end_ms": 4300,
      "text": "您好，请问今天想咨询什么？",
      "confidence": 0.96,
      "speaker": "asr_hint"
    }
  ],
  "metadata": {}
}
```

`id/start_ms/end_ms/text` 必填；`confidence/speaker` 可选，其他句段字段保存在结果的 `source`。敏感名称字段的值和完整主机路径在写入 `source` 或 `metadata` 前脱敏。句段按开始时间稳定排序，允许重叠；重复 ID、非法区间和音频越界被拒绝。ASR speaker 只用于保存和回显，不进入任何算法阶段。

## 输出

`result.json` 使用 `voice_analysis_result_v1`，包含：

- `status`：`success` 或 `partial`；算法拒识产生的 `unknown` 仍可属于完整成功；
- `audio/configuration/models/components`：脱敏后的输入、配置、模型和组件摘要；
- `speakers`：录音内 `local_spk_n` 及质量、半径和来源摘要，不包含 embedding；
- `segments`：`source`、规范字段和 `assignment`；
- `audit`：候选区、窗口、批次、聚类、归属及资源证据。

`assignment` 固定包含 `label/status/source/level/confidence/reason/risk/evidence/policy_version`。未知标签固定为 `unknown`。TXT、SRT、VTT 只读取提交完成的 `result.json` 派生，默认以 `[local_spk_n]` 或 `[unknown]` 为前缀。

显式截止时间在每个阶段开始和结束时检查，8077/8078 请求同时使用剩余时间作为超时；任何阶段越过截止时间均以退出码 `21` 失败，不把迟到结果作为成功返回。

## 评测 Manifest

JSONL 每行至少包含 `id/reference_path/scenarios`，并使用以下两种输入之一：

- `audio_path + segments_path`：执行真实 M1 引擎；
- `result_path`：只评分已有权威结果。

参考标注是句段数组或带 `segments` 的对象，每个句段包含 `start_ms/end_ms/speaker`。Manifest 行可增加 `source_recording_id` 和 `annotation_grade`：多个派生样本共享同一 `source_recording_id` 时只计为一个独立录音；显式 `weak_*` 标注等级不具备正式门槛资格。报告按 `config/evaluation.yaml` 输出执行样本数、独立录音数、标注等级、资格原因、每份引擎状态、逐录音指标和全部逐门槛实际值；数据不足或弱标注时状态固定为 `insufficient_dataset`，但仍计算观察指标。
