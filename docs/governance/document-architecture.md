# Voice Analysis 文档职责与写入规则

状态：current

核验日期：2026-08-31

## 1. 文档入口与裁决顺序

`docs/README.md` 是唯一文档入口。发生冲突时按以下顺序裁决：

1. 用户最新明确要求；
2. `docs/requirements` 中仍适用的后发需求；
3. 当前代码、配置、模型清单和测试证据；
4. `docs/architecture`、`docs/contracts` 和 `docs/operations`；
5. `docs/development` 和 `docs/history`，只用于追溯。

## 2. 文档类型

- `requirements`：实施前记录用户原话、目标、范围和验收；
- `development`：实现和验证结束后记录实际结果、偏差和未验证项；
- `architecture`：只写当前已实现的进程、边界、数据流和明确未实现项；
- `contracts`：只写当前公开 HTTP、Schema、鉴权、错误和状态语义；
- `operations`：只写当前能够执行并已验证的本地或部署操作；
- `history`：保存 Smart Badge 历史来源，不裁决当前实现；
- 根 `PROJECT_STATUS.md`：面向本项目的当前资产清单和后续产品开发范围。

## 3. 固定写入时点

- 行为、接口、配置、模型资产、脚本或测试变化，在首次实现写入前建立需求记录并更新入口；
- 实现和验证结束后一次性更新开发记录及受影响的架构、契约、操作和导航文档；
- 未执行的测试、容器构建或部署必须写为未验证，不得由代码存在推断成功；
- 历史原文不追溯改写，后续事实通过新需求和新开发记录覆盖。

## 4. 当前安全边界

文档不得保存 API key、Hugging Face token、真实录音、完整 embedding 或身份材料。模型清单可以记录公开模型 ID、来源提交、文件大小和 SHA256，但模型权重只进入被 Git 忽略的本地目录或后续明确设计的制品系统。
