# BC-014：跨 Evidence 字段召回不足

## 元信息

- 状态：修复中
- 首次发现：2026-07-15
- 主要分类：MODEL_OUTPUT
- 次要分类：EVIDENCE_RETRIEVAL、EVALUATION_REGRESSION
- 关联模块：ClaimBuilder Prompt、Entity Golden Evaluation
- 发现 Run：`qwen-plus-bc005-primary-evidence-regression`

## 预期结果

同一工作项的行动记录、决策和风险证据分散在不同 Evidence 时，实体应聚合明确字段，并将字段 Evidence 同时加入父 Claim refs。

## 实际结果

BC-005 最终回归已经 passed，Evidence、Claim、Citation、Entity Field Support 和 Action/Risk Entity Recall 均为 100%。字段 Golden 仍显示：

- Action Owner Precision/Recall：100% / 100%；
- Action Due Date Precision/Recall：100% / 50%；
- Risk Owner Precision/Recall：100% / 100%；
- Risk Mitigation Recall：0%。

支付 API 行动项没有关联另一条“截止日期定为本周五”的决策 Evidence；告警风险没有抽取同一句中的“排查原因”作为 mitigation。

## 影响

系统已能安全生成结构化字段，但跨来源字段不完整。若直接同步任务系统，会遗漏一个明确截止日期和一个明确风险响应动作。

## 根本原因

ClaimBuilder 主要按单条 Evidence 构建实体，Prompt 没有要求在字段为空前扫描同主题 Evidence，也没有强调 parent refs 与 field refs 的跨来源扩展。

## 第一阶段修复

- Prompt 要求字段为空前检查所有同项目/同主题 Evidence；
- 允许跨来源字段，但字段 Evidence 必须加入父 Claim refs；
- 优先使用专门行动记录作为 primary Claim，再用决策 Evidence 补日期；
- 明确“排查原因”“降级发布”等具体响应短语可作为 mitigation；
- 禁止跨项目 ID、不同工作项或不同负责人错误合并；
- Golden 改成穷举 4 个 ActionItem、2 个 Risk，开始计算实体 Precision。

## 回归状态

Prompt 与 Golden 已更新，Stub 穷举实体 Precision/Recall 基线通过；尚未再次调用 qwen-plus，因此状态为“修复中”。

## 面试复盘要点

字段值有 Evidence 不代表字段完整。Support/Precision 解决“有没有编造”，Recall 解决“有没有漏掉”；企业 Agent 必须同时评测两者。

