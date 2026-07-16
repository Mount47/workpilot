# BC-016：专用行动 Evidence 被合并

## 元信息

- 状态：修复中，离线通过待真实回归
- 首次发现：2026-07-16
- 主要分类：CLAIM_GRANULARITY
- 次要分类：MODEL_OUTPUT、ENTITY_RECALL
- 关联模块：ClaimBuilder、EntityBuilder、Claim Source Coverage
- 发现 Run：`qwen-plus-bc015-canonical-candidate-regression`

## 预期结果

会议纪要中每条专用行动记录都应形成独立 Claim。相关 blocker、decision 和 action 可以共享主题，但不能因为语义相关就合并成一个 Claim，否则责任跟踪和实体评测会丢失粒度。

## 实际结果

模型把 E0013“李四输出支付 API 设计文档”合并进 C0008“李四本周给出方案”的 refs，最终只有 C0008，没有 E0013 对应的独立 Claim：

- Claim Source Coverage 仍为 100%，因为 E0013 出现在某个 Claim refs 中；
- EntityBuilder 只能看到 3 个有效 Action Candidate；
- Action Precision 100%，Recall 75%；
- 引用与字段验证全部通过，因此普通 Verifier 无法发现业务粒度丢失。

## 根本原因

“Evidence 被引用”不等于“Evidence 的独立业务记录被保留”。现有 Source Coverage 只验证来源覆盖，没有验证专用 Action Evidence 与 Action Claim 的一一投影约束。

## 修复

- ClaimBuilder 物化模型 Draft 后扫描所有 `action_item` Evidence；
- 若没有 Action Claim 的 text 与该 Evidence quote 精确一致，确定性追加 explicit Action Claim；
- 同时追加最小 ActionItem，字段暂为 unknown，交给独立 EntityBuilder 补全；
- Trace 增加 `dedicated_action_recovery_count`；
- 相关但不同的 blocker/action 不再因共享 refs 丢失独立记录。

## 验证

- 新增模型合并两条 Action Evidence 时的恢复测试；
- 自动化测试 191 passed；
- 尚待 qwen-plus Golden 回归，因此状态仍为修复中。

## 面试复盘要点

覆盖率指标可能出现 false pass。企业 Agent 不只要验证“来源有没有被用到”，还要按业务实体类型验证信息粒度是否被保留。
