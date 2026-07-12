# 受限 LLM Planner 与确定性降级

## 基本信息

- 日期：2026-07-12
- 状态：已完成
- 关联模块：Planning、Provider Routing、Runtime、Mission Contract、Trace

## 背景

当前 DeterministicPlanner 会生成固定六步计划，安全且可复现，但无法根据业务目标调整步骤描述、输入和成功标准。Provider 运行策略已经具备 `planning` TaskType、能力路由、重试和故障转移，可以在此基础上引入受限 LLM Planner。

当前 Runtime 只实现六个高层工具 Handler，不能安全执行任意模型生成的新工具或任意 DAG。因此本阶段不宣称实现开放式自主规划，而是实现“模型起草、系统收口、确定性验证、失败降级”的受控规划闭环。

## 目标

1. 模型只能从 Tool Registry 中选择工具；
2. Plan 的 goal、plan_id、created_by 由系统赋值，模型不能覆盖；
3. 模型输出 PlanDraft，转换后继续使用现有 Plan Schema；
4. PlanValidator 检查工具、权限、依赖、循环和步骤预算；
5. RuntimePlanPolicy 检查当前执行器要求的步骤 ID 与工具映射；
6. 瞬时 Provider 失败、非法结构和非法计划可以回退 DeterministicPlanner；
7. 认证和明显配置错误必须暴露，不能被降级路径掩盖；
8. Planner 模型调用、选择结果和降级原因进入 Trace。

## 非目标

- 不允许模型生成任意代码或注册新工具；
- 不允许模型删除 Evidence、Verify 或 Finalize 安全步骤；
- 不实现任意 DAG 的自动调度；
- 不实现跨 Run 计划经验学习；
- 不默认启用 LLM Planner；只有配置 `planning` ModelRoute 时才尝试使用。

## PlanDraft

模型只返回步骤草稿：

```text
step_id
objective
tool
inputs
dependencies
expected_output
success_criteria
evidence_required
```

以下字段由系统生成：

```text
plan_id
goal
created_by
validated
created_at
status
attempts
last_error_type
```

这样可以防止模型篡改任务目标、运行身份和执行状态。

## 当前 Runtime 兼容策略

第一版要求计划恰好包含：

```text
scan_workspace    -> workspace.scan
extract_evidence  -> evidence.extract
build_claims      -> claims.build
render_artifacts  -> artifacts.render
verify            -> verification.run
finalize          -> artifacts.finalize
```

模型可以调整 objective、inputs、dependencies、expected_output 和 success_criteria，但依赖必须通过通用 PlanValidator，执行时仍由 PlanExecutor 检查前置步骤状态。

后续只有在 Runtime 建立统一 Tool Handler/ToolResult 和 DAG Scheduler 后，才逐步放宽步骤集合。

## 降级规则

允许回退 DeterministicPlanner：

- 可重试 Provider 错误在 RetryPolicy 和 Route Failover 后仍失败；
- ProviderResponseError；
- PlanValidationError；
- RuntimePlanCompatibilityError。

禁止静默回退：

- authentication；
- 未知配置或编程错误；
- BudgetExceededError；
- 用户未授权的工具或数据访问企图需要在 Trace 中保留拒绝原因。

## Trace

新增：

```text
planner_decision
  requested: llm | deterministic
  selected: llm | deterministic
  fallback_reason
  plan_id
```

Planning 模型调用继续使用版本化模型 Trace：

```text
task_type=planning
prompt_version=planner.v1
schema_name=PlanDraft
schema_version=plan.v1
```

不记录完整 Prompt 和模型原始计划正文。

## 验收标准

- 合法 PlanDraft 可以进入现有 PlanExecutor；
- 未知工具、缺少必要步骤、工具映射错误、缺失依赖和循环计划均被拒绝；
- 可降级错误生成确定性计划并记录原因；
- 认证和预算错误不被吞掉；
- Planning 模型 Token 进入统一预算；
- 未配置 planning Route 时行为与现有版本一致；
- 完整回归和最小 Stub Eval 通过。

## 实际结果

- PlanDraft 和 PlanStepDraft 已实现 `extra=forbid`，模型不能注入 Goal 或执行状态；
- ConstrainedLLMPlanner 只向模型暴露 Contract 允许的 ToolSpec；
- RuntimePlanPolicy 已限制当前六个必要步骤、工具映射和依赖方向；
- FallbackPlanner 已区分可降级错误与认证、预算等不可隐藏错误；
- `planning` ModelRoute 已进入 Runtime，并记录 planner_decision 和版本化模型调用；
- 未配置 planning Route 时原有确定性行为保持不变；
- 完整回归为 97 passed、总体覆盖率约 89%；
- 最小 Stub Eval 保持通过。

## 遗留限制

- 当前 Runtime 仍按固定代码顺序调用六个 Handler，模型不能真正增删步骤；
- success_criteria 只是计划契约，尚未被执行器自动判定；
- 尚未建立真实模型下 deterministic 与 LLM Planner 的对比评测；
- 计划调整、跳过、取消、并行和检查点恢复尚未实现。
