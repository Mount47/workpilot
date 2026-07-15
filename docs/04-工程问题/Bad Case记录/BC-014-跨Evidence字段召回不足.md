# BC-014：跨 Evidence 字段召回不足

## 元信息

- 状态：修复中
- 首次发现：2026-07-15
- 主要分类：MODEL_OUTPUT
- 次要分类：EVIDENCE_RETRIEVAL、EVALUATION_REGRESSION
- 关联模块：ClaimBuilder Prompt、EntityBuilder、Entity Golden Evaluation
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

## 第一阶段真实回归

2026-07-15 使用 `qwen-plus-bc014-cross-evidence-regression` 回归，Run passed，引用和字段支持仍为 100%，但结果只部分改善：

- Risk Mitigation Precision/Recall 从无召回提升为 100% / 100%；
- Action Entity Recall 从 100% 降至 75%；
- Action Due Date Recall 从 50% 降至 33.33%；
- Risk Precision 为 66.67%，模型把同一支付风险重复投影；
- Risk Owner Precision 为 50%，相关行动负责人被错误当成重复风险负责人；
- 5 类证据/引用/Claim/字段支持指标仍为 100%，说明“已填字段有依据”不能防止实体漏召回和语义错配。

本次结果证明继续堆叠 ClaimBuilder Prompt 不足以稳定解决问题，BC-014 保持“修复中”。

## 第二阶段修复

- 新增独立 `EntityBuilder`，把不可变 Claim 生成与业务实体投影解耦；
- EntityBuilder 查看完整 Claim 和 Evidence 集合，独立输出 ActionItem/Risk 投影；
- 允许从 blocker/decision/context 中识别明确行动义务，不再只依赖 Claim category；
- 跨 Evidence 字段引用自动并入 entity source refs；
- 未知 Claim/Evidence 引用 fail closed；
- 同一种实体禁止对同一个 Claim 重复投影；
- Trace 新增 action/risk 数量及实体投影物理模型调用数；
- 原有 EntityFieldVerifier 继续负责字段原文支持和引用边界验证。

代码回归为 179 passed。该阶段尚需再次调用 qwen-plus 验证，不能提前标记修复完成。

## 面试复盘要点

字段值有 Evidence 不代表字段完整。Support/Precision 解决“有没有编造”，Recall 解决“有没有漏掉”；企业 Agent 必须同时评测两者。

本 Case 还证明：当一个模型调用同时承担事实抽取、分类、实体发现、跨证据合并和去重时，Prompt 局部改动可能改善一个指标却损害另一个指标。拆分阶段的价值不只是代码整洁，而是让失败可定位、可独立评测和可定向重试。
