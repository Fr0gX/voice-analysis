# Smart Badge 历史来源

状态：historical evidence

整理日期：2026-08-27

本工作区的初始技术参数来自相邻 Smart Badge 仓库的以下证据：

- 当前历史说明：`smart_badge/docs/history/sound-analysis/retired-speaker-separation-pipeline.md`。
- 退役前最后可读取的组件文件：Smart Badge Git 提交 `8b4aeb2536e985dc3be4db522476111b8d167352` 下的 `voiceprint_service` 和 `window_refine_service`。
- 删除证据：提交 `58fd17c6ea42a624b9a4598dea7f5ccaee557a6d` 删除上述两个组件；Smart Badge 当前架构明确不生成、匹配或消费声纹。

从历史证据继承的只有端口、模型、算法输入、资源上限和依赖边界。没有复制 Smart Badge 业务代码、数据库模型、配置中心、录音数据、声纹数据或业务密钥。新仓库的鉴权、目录和发布边界必须由自己的后续需求裁决。
