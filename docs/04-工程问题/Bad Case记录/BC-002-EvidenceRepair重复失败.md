# BC-002：Evidence Repair 重复无效候选且未恢复

## 元信息

- 状态：已回归验证
- 首次发现：2026-07-14
- 最后更新：2026-07-14
- 主要分类：AGENT_LOOP
- 次要分类：EVIDENCE_RETRIEVAL、PERFORMANCE_COST
- 关联模块：Runtime、EvidenceExtractor、OpenAI-compatible Provider
- Provider / Model：阿里云百炼 / qwen-plus
- Run：`runs/qwen-plus-bc001-regression`
- 关联评测 Case：`evals/BC-001真实回归.json`、BC-001

## 用户任务与输入概况

在 BC-001 相同脱敏 Workspace 和 Goal 上，验证新增 Evidence Quality Gate 与定向 Repair。

## 预期结果

首次某来源候选全部丢弃后，Repair 应根据失败原因改善 exact quote 或 locator，至少恢复一条正式 Evidence；无法恢复时必须有界失败并记录成本。

## 实际结果

- 首轮：13 个候选，接受 3 个，丢弃 10 个；
- 一个来源触发 Repair；
- Repair 调用 1 次，消耗 862 Token、模型延迟 6563.54 ms；
- 第二轮门禁仍显示 13 个候选、接受 3 个、丢弃 10 个；
- Repair Recovery Rate 为 0%；
- Runtime 以 `evidence_quality_failed` 正确终止。

## 证据

Trace 顺序：

```text
evidence_quality_evaluated attempt=1 passed=false
evidence_repair_requested source_count=1
model_call_completed total_tokens=862
evidence_quality_evaluated attempt=2 passed=false
tool_call_failed error_type=evidence_quality_failed
run_failed
```

## 影响范围与严重级别

- 严重级别：中；
- 正确性：系统已 fail closed，没有输出错误报告；
- 可用性：任务仍无法完成；
- 成本：增加一次无效模型调用和 862 Token；
- 延迟：增加约 6.56 秒模型延迟。

## 直接原因

第二次模型调用仍然产生不能通过 exact quote/locator 校验的候选。

## 根本原因

当前 Repair 只在 Goal 后追加通用指令，没有提供：

- 上轮候选的安全错误类型分布；
- 哪些候选是 quote 不存在、行号错误或行窗口错误；
- 可执行的字段级修复要求；
- 确定性 quote 重定位结果。

同一 Provider、相同源内容和近似 Prompt 很容易重复同类错误。

## 最终修复

第一版修复代码与自动化测试已经完成：

1. exact quote 存在但 locator 错误时由代码确定性重定位；
2. 重复 quote 选择距离模型建议行号最近的位置；
3. Prompt 输入增加 `N | source line` 行号视图；
4. Repair Prompt 携带脱敏失败原因计数和字段要求；
5. Trace 和 Eval 增加 locator 修复次数与原因分布；
6. 仍保持最多一次 Repair。

真实模型回归已经完成。最终 15 个 Candidate 全部接受，没有触发模型 Repair；Locator Repair Count 也为 0，说明本次主要收益来自输入行号视图和更明确的 marker 约束，而不是运行时重定位兜底。

## 回归测试

已有“第一次无效、第二次有效”、错误原因反馈、错误 locator 重定位、重复 quote 最近位置和 quote 不存在仍拒绝的测试。

## 真实场景复跑

已使用独立 `evals/BC-001真实回归.json` 复跑，输出保存在 `runs/qwen-plus-bc002-regression`，没有覆盖失败产物。

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| Evidence Recall | 20% | 100% |
| Evidence Acceptance | 23.08% | 100% |
| Evidence Discard | 76.92% | 0% |
| Candidate / Accepted | 13 / 3 | 15 / 15 |
| Locator Repair Count | 未实现 | 0 |
| Model Repair Trigger | 1 | 0 |
| Repair Token | 862 | 0 |
| Claim Revision | 未进入 Claim 阶段 | 1 |
| Physical Model Calls | 3 | 4 |
| Total Token | 2422 | 6581 |
| Runtime Elapsed | 20.92 秒 | 47.38 秒 |
| Final Status | failed | passed |

修复后总 Token 和耗时更高，主要因为系统不再提前失败，继续完成了 Claim 生成、15 个验证错误的业务修订和最终验证。Evidence 两次调用合计 1935 Token；失败版本首次 Evidence 加 Repair 三次调用合计 2422 Token，两者不能只用 Run 总成本直接判断 Prompt 开销。

## 关联代码与提交

- `afd0197 feat(evidence): add quality gates and bounded repair`；
- `d088611 feat(eval): measure evidence quality and repair cost`。
- `b489611 fix(evidence): repair locators and enrich retry feedback`。

## 面试复盘要点

增加重试不等于问题得到修复。第一轮通用 Repair 多消耗 862 Token但没有提升接受率；第二版先加入模型可直接遵循的行号视图，同时保留确定性 locator 兜底和一次上限。相同 qwen-plus 回归中 15 条候选全部接受，模型 Repair 不再触发。Trace 还表明 Locator Repair Count 为 0，因此我能区分本次收益来自 Prompt 协议改善，而不是把所有成功都归因于新增代码兜底。
