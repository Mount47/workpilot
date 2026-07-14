# BC-002：Evidence Repair 重复无效候选且未恢复

## 元信息

- 状态：修复中
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

真实模型尚未复跑，因此状态保持“修复中”。

## 回归测试

已有“第一次无效、第二次有效”、错误原因反馈、错误 locator 重定位、重复 quote 最近位置和 quote 不存在仍拒绝的测试。

## 真实场景复跑

本案例本身来自真实 qwen-plus 复跑。完成下一版修复后，使用独立的 `evals/BC-001真实回归.json` 在新目录再次运行，不能覆盖本次失败产物。

## 关联代码与提交

- `afd0197 feat(evidence): add quality gates and bounded repair`；
- `d088611 feat(eval): measure evidence quality and repair cost`。
- `b489611 fix(evidence): repair locators and enrich retry feedback`。

## 面试复盘要点

增加重试不等于问题得到修复。真实回归显示，通用 Repair Prompt 多消耗 862 Token，但候选接受率没有提升。我保留了 fail-closed 和次数上限，并把下一步拆成确定性 locator 修复、错误反馈 Prompt 和 attempt 差异评测，避免用无限重试掩盖模型稳定性问题。
