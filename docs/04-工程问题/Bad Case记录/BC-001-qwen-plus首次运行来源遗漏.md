# BC-001：qwen-plus 首次运行来源遗漏但 Run passed

## 元信息

- 状态：修复中
- 首次发现：2026-07-14
- 最后更新：2026-07-14
- 主要分类：EVIDENCE_RETRIEVAL
- 次要分类：VERIFICATION_GAP、AGENT_LOOP、MODEL_OUTPUT
- 关联模块：EvidenceExtractor、Runtime、ClaimBuilder、Verifier、Evaluation
- Provider / Model：阿里云百炼 / qwen-plus
- Run：`runs/qwen-plus-first`
- 关联评测 Case：`basic_project_weekly_report`

## 用户任务与输入概况

任务为根据脱敏 `basic_project` 生成本周项目周报。Workspace 包含：

- `issues.md`；
- `meeting_notes.md`。

两个来源分别包含任务状态，以及会议决策、风险和行动项。

## 预期结果

- 两个来源均被处理；
- 关键进展、风险、决策和行动项进入结构化 Claims；
- 所有事实具有有效 Evidence；
- 信息缺失时系统不能误判为 passed。

## 实际结果

- Run 最终状态为 passed；
- 扫描 2 个来源；
- 模型产生 13 个 Evidence Candidate，只接受 3 个；
- 10 个候选因 exact quote 或 locator 校验被丢弃；
- 最终 Snapshot 只有 `issues.md` 进入有效结论；
- 会议纪要中的决策和行动项没有进入最终报告；
- 首轮验证出现 3 个 error，业务修订 1 次后通过；
- 物理模型调用 4 次，总 Token 3682。

## 证据

### Trace

`runs/qwen-plus-first/trace.json` 显示：

- `workspace_scanned.file_count = 2`；
- `evidence_extracted.count = 3`；
- `evidence_extracted.discarded = 10`；
- 共 4 个 `model_call_completed`；
- 首轮 `verification_completed.error_count = 3`；
- 1 个 `revision_requested`；
- 第二轮 verification passed。

### Artifact

- `project_snapshot.json` 的最终有效来源覆盖不完整；
- `weekly_report.md` 缺少会议决策和行动项；
- `action_items.json` 为空；
- `verification_report.json` 最终 passed。

### 指标

| 指标 | 修复前结果 |
|---|---:|
| Source count | 2 |
| Evidence Candidate | 13 |
| Accepted Evidence | 3 |
| Evidence Acceptance Rate | 23.08% |
| Evidence Discard Rate | 76.92% |
| Physical Model Calls | 4 |
| Total Tokens | 3682 |
| Business Revisions | 1 |
| Final Status | passed，但业务不完整 |

## 影响范围与严重级别

- 严重级别：高；
- 影响：企业报告可能遗漏会议决策、风险和责任事项；
- 风险：用户看到 passed 后会误认为报告完整；
- 安全影响：无直接越权，但会产生业务决策风险。

## 复现步骤

使用百炼 qwen-plus，对 `tests/fixtures/workspaces/basic_project` 执行“生成本周项目周报”，检查 Trace 中 Evidence 候选接受情况，并对比扫描来源与最终 Claim 引用来源。

## 直接原因

模型生成的多个 Candidate 没有严格复制原文或给出正确行号，因此被确定性校验拒绝；ClaimBuilder 只基于剩余 Evidence 生成结果。

## 根本原因

1. EvidenceExtractor 丢弃无效候选后没有触发修复；
2. Runtime 只记录总数，不知道哪个来源失败；
3. Verifier 检查已生成 Claim 的正确性，不检查来源覆盖；
4. passed 的语义偏向 Precision，没有包含 Recall 和 Coverage；
5. Eval Runner 没有 Source Coverage、Acceptance 和 Repair 指标。

## 备选修复方案

- 放宽 quote/行号验证：会降低可追溯性，不采用；
- 所有来源零 Evidence 都失败：会误伤无关文件，不采用；
- 无限制重试模型：成本和终止性不可控，不采用；
- 有界、定向修复并增加来源覆盖验证：采用。

## 最终修复

已实现第一版代码防线：

- 每来源 `SourceExtractionReport`；
- Source Coverage Gate；
- Evidence Acceptance Gate；
- 问题来源最多一次 Evidence Repair；
- 第二次仍失败则 `evidence_quality_failed`；
- `SourceCoverageVerifier` 检查 Evidence 来源是否进入 Claim；
- 门禁和修复进入安全 Trace；
- Eval Runner 增加覆盖、接受、丢弃、恢复和成本指标。

## 回归测试

已覆盖：

- 候选全部丢弃触发修复；
- 第一次无效、第二次有效后 Runtime passed；
- 修复 Evidence ID 不冲突；
- Provider 显式错误被门禁发现；
- Claim 遗漏 Evidence 来源时验证失败；
- Repair 调用和 Token 只统计修复窗口。

## 真实场景复跑

待使用相同 qwen-plus、相同 Workspace 和相同 Goal 复跑。完成前本案例保持“修复中”，不能标记为已回归验证。

## 修复前后对比

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| Source Coverage Rate | 未统计 | 待复跑 |
| Evidence Acceptance Rate | 23.08% | 待复跑 |
| Evidence Discard Rate | 76.92% | 待复跑 |
| Claim Source Coverage Rate | 不完整 | 待复跑 |
| Evidence Repair Trigger | 0 | 待复跑 |
| Evidence Repair Recovery | 不适用 | 待复跑 |
| Physical Model Calls | 4 | 待复跑 |
| Total Tokens | 3682 | 待复跑 |
| Final Status | passed，但不完整 | 待复跑 |

## 关联代码与提交

- `afd0197 feat(evidence): add quality gates and bounded repair`；
- `02328b7 docs(evidence): document quality gates and repair loop`；
- 本轮 Eval Runner 指标提交将在实现完成后补入。

## 遗留问题

- 零候选来源目前只 warning；
- 尚无按 Evidence 类型的任务要求覆盖；
- Evidence Repair 仍使用同一 Provider；
- Planner 尚不能根据错误替换工具；
- 真实模型修复效果和额外成本待验证。

## 面试复盘要点

首次真实模型链路最终 passed，但 Trace 显示 Evidence 接受率只有 23.08%，并且会议来源没有进入报告。这暴露了 Precision 通过不等于 Recall 完整的问题。我没有放宽引用校验或无限重试，而是增加了每来源质量报告、生成前双门禁、最多一次定向 Evidence Repair，以及 Evidence 到 Claim 的来源覆盖验证。自动化回归已证明错误可被发现和恢复，下一步用相同模型复跑并量化覆盖收益与 Token 成本。
