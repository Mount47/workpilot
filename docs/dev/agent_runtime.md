# Agent Runtime

## Runtime 职责

Runtime 拥有控制流，负责：

- 创建 mission contract。
- 每一步执行前做 contract 校验。
- 调用 planner、tools、synthesizer、verifier。
- 记录 trace events。
- 在预算耗尽时停止。
- 最多执行有限次数的修正 pass。
- 最终写出 artifacts 和 verification status。

Runtime 不应把循环控制权交给 LLM。

## 状态机

```text
pending
  -> planning
  -> retrieving
  -> synthesizing
  -> verifying
  -> revising
  -> verifying
  -> passed

failed 可以从任意 active state 进入。
cancelled 只由明确的用户中断进入。
```

MVP 可以允许 verifier 失败后进行一次修正。更多修正次数必须由 mission contract 显式配置。

## 主流程

1. 创建 run。
2. 创建 mission contract。
3. 写入 `run_started` trace event。
4. 向 planner 请求结构化 plan。
5. 按 contract 校验 plan。
6. 扫描 workspace。
7. 分类 source files。
8. 抽取并注册 evidence snippets。
9. 生成 `weekly_report.md`。
10. 生成 `risks.json`。
11. 生成 `action_items.json`。
12. 运行 verifier。
13. 如果 verification 失败且允许修正，则用 verifier errors 和 evidence store 修正 artifacts。
14. 再次运行 verifier。
15. 写入最终 artifacts、`verification_report.json`、`trace.json`。
16. 标记 run 为 `passed` 或 `failed`。

## Step Contract Check

每个 step 执行前，Runtime 检查：

- step count 未超过 `max_steps`。
- time budget 仍有剩余。
- token budget 仍有剩余。
- step type 被允许。
- tool 被允许。
- 文件路径在 workspace 内。
- 输出 artifact type 被允许。
- 未请求 forbidden action。

被拒绝的 step 应写入 trace，并记录明确原因。

## Tool Call 生命周期

每次工具调用遵循：

```text
prepare_input
  -> validate_against_contract
  -> execute
  -> normalize_output
  -> persist_result
  -> append_trace_event
```

工具输出应在 trace 中摘要记录。大段完整内容应进入 artifact 或 evidence record，而不是复制到每条 trace event。

## Evidence 抽取

Evidence extraction 可以采用混合方式：

- 确定性搜索关键词、标题、日期、issue ID、PR ID。
- LLM 辅助提出候选片段。
- Runtime 将候选规范化为 evidence records。
- Citation verifier 确认 quote 确实存在于 source。

可信单元不是 LLM 的抽取响应，而是通过验证并持久化的 evidence row。

## Synthesis 约束

Synthesizer 接收：

- Goal
- Artifact schemas
- Source metadata
- Evidence entries
- 修正阶段的 previous verifier errors

Synthesizer 不能在最终 claim 中直接引用原始 file path，而必须引用 evidence store 中的 `evidence_id`。

## 失败模式

Runtime 遇到以下情况应 fail closed：

- Workspace path validation 失败。
- 必需 artifacts 缺失。
- Citation verifier 在允许修正后仍失败。
- Action item schema validation 在允许修正后仍失败。
- token、时间或 step 预算耗尽。
- LLM provider 返回 malformed structured output，且修复尝试耗尽。

## Trace 要求

Trace 应使 run 可解释。至少包含：

- 原始 goal。
- contract。
- 生成的 plan。
- 扫描过的文件。
- 抽取的 evidence。
- 生成的 artifacts。
- verifier results。
- revision attempts。
- final state。

