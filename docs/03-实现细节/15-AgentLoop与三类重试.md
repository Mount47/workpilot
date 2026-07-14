# Agent Loop 与三类重试实现

## 对应代码

- `src/workpilot/runtime/runner.py`
- `src/workpilot/planning/scheduler.py`
- `src/workpilot/planning/executor.py`
- `src/workpilot/providers/openai_provider.py`
- `src/workpilot/providers/retry.py`
- `src/workpilot/providers/routing.py`

## 当前定位

WorkPilot 当前不是允许模型无限选择动作的开放式 ReAct Loop，而是一个可执行、可观测、有预算上限的受控 Agent Loop：

```text
Plan
  -> Execute validated DAG
  -> Build evidence-grounded state
  -> Render deterministically
  -> Verify
  -> Revise bounded steps when necessary
  -> Finalize
```

这种设计牺牲了一部分自由探索能力，换取：

- 工具白名单；
- 固定预算上限；
- 可复现调度；
- 失败传播；
- 每轮修订可追踪；
- 最终产物必须通过验证门。

## 外层 Run 生命周期

```text
Runtime.execute()
  ├── start budget
  ├── append run_started
  ├── _run_pipeline()
  ├── success -> passed
  ├── exception -> failed
  └── finally
      ├── budget_summary
      ├── run_context.json
      └── trace.json
```

无论成功还是失败，finally 都会输出安全上下文和 Trace。当前还不能从这些文件恢复执行，它们是观测产物，不是完整 Checkpoint。

## 第一层：Planning

Runtime 调用 Planner 生成 Plan：

- 未配置 planning Route：DeterministicPlanner；
- 配置 planning Route：ConstrainedLLMPlanner；
- LLM Plan 非法或发生可降级错误：FallbackPlanner；
- 认证、预算和未知编程错误：不静默降级。

Plan 必须经过：

```text
PlanValidator
  -> RuntimePlanPolicy
  -> PlanExecutor / Scheduler
```

当前 RuntimePlanPolicy 仍要求六个核心步骤，因此 Planner 可以调整描述和合法依赖，但不能任意增加新工具。

## 第二层：DAG 执行循环

SerialDAGScheduler 每轮：

```text
传播 dependency terminal 状态
  -> 查找 ready steps
  -> 按 Plan 顺序执行
  -> 保存 ToolResult
  -> 失败分支后继 blocked
  -> 独立分支继续
  -> 无 ready 且仍 pending 则 deadlock
```

当前六步 DAG：

```text
scan_workspace
  -> extract_evidence
     -> quality gate
     -> bounded evidence repair when necessary
  -> build_claims
  -> render_artifacts
  -> verify
  -> finalize
```

Scheduler 第一次运行时在 finalize 前暂停。Runtime 读取 verify 的业务结果，决定是否修订；验证通过或修订耗尽后，再恢复 Scheduler 执行 finalize。

## 第三层：业务验证与修订循环

伪代码：

```python
attempt = 1
run_dag_until_before_finalize()

while verification_errors and attempt < 3:
    feedback = format_verification_feedback(errors)
    memory.request_revision(feedback)
    attempt += 1

    reenter("build_claims")
    reenter("render_artifacts")
    reenter("verify")

resume_dag_finalize()
```

修订只允许重入 ToolSpec.reentrant=true 的三个步骤：

```text
claims.build
artifacts.render
verification.run
```

Workspace 扫描和 Evidence 抽取不会因 Claim 验证失败而整体重复。Evidence 自身的 quote、行号、Provider 和全局空结果问题会在 Claims 生成前进入独立的有界修复；语义召回不足目前仍不能自动补救。

## 第四层：结构化输出重试

OpenAI-compatible 和 Claude Adapter 在以下情况重新调用模型：

- 返回内容不是合法 JSON；
- Pydantic Schema 校验失败；
- 字段类型或枚举不符合契约。

当前 Provider 默认 `max_retries=1`，所以单次结构化操作最多两次物理模型调用：

```text
call 1
  -> parse/validate failed
  -> build safe field-path/error-type feedback
  -> call 2 with previous response and correction request
  -> still failed: ProviderResponseError
```

最终异常不包含 Raw response 或 Pydantic input，避免业务正文经异常进入 Trace。普通错误只返回路径与类型；代码白名单中的静态校验消息可以进入反馈，帮助模型区分 action/risk 嵌套对象等跨字段约束。该改造来自 BC-006 与 BC-007。

如果首轮验证已完成、后续修订异常，Runtime 会在 finally 中保存 status=`incomplete` 的最后一轮 `verification_report.json`，避免失败 Run 丢失验证指标，见 BC-009。

Evidence JSON 数组解析也使用类似循环，但最终失败时返回带 `malformed_response` 的空候选结果，而 Claim 结构失败会抛出异常。两者失败语义目前不完全一致。

## 第五层：Transport Retry 与 Failover

配置 ModelRoute 后，RetryPolicy 处理统一 ProviderCallError：

可重试：

```text
timeout
network
rate_limit
server
```

不可静默重试或切换：

```text
authentication
budget_exceeded
invalid_request
unknown programming error
```

RetryPolicy 使用有上限的指数退避，每次物理尝试前重新检查 Runtime Budget。重试耗尽且 Route 允许 Failover 时，才进入下一个 ModelTarget。

未配置 ModelRoute 的单 Provider运行不会经过 WorkPilot 的 Route RetryPolicy。底层 SDK 是否执行内部重试目前没有进入 WorkPilot Trace，这是一个可观测性限制。

## 三类“再调用模型”的区别

| 类型 | 触发条件 | Prompt | 控制层 | 当前上限 |
|---|---|---|---|---:|
| Transport Retry | 超时、网络、限流、服务端错误 | 不变 | ModelRouter / RetryPolicy | Route 默认 3 attempts |
| Structured Retry | JSON/Pydantic 失败 | 增加脱敏 Schema 校验反馈 | Provider Adapter | 1 retry |
| Business Revision | Claim/Citation error | 增加验证反馈 | Runtime | 总计 3 synthesis attempts |
| Evidence Repair | 来源读取/Provider/候选接受门禁失败 | 增加精确 quote 与行号修复指令 | Runtime Evidence Gate | 总计 2 extraction attempts |

Evidence Candidate 的 exact quote 存在但 locator 错误时先由代码确定性重定位，不增加模型调用。quote 不存在等剩余失败导致某来源候选全部丢弃时，只对该来源额外调用一次模型，并携带脱敏失败原因计数。

## 终止条件

Run 在以下情况终止：

- DAG 全部完成且验证无 error：passed；
- 三次生成后仍有 error：failed；
- Step/Token/Time Budget 超限：failed；
- Provider 认证或不可恢复错误：failed；
- Scheduler deadlock：failed；
- Tool success rule 失败：对应 Step failed，后继 blocked，Run failed。

因此不存在无限循环。

## 本次 qwen-plus 运行实例

```text
planning: deterministic
scan_workspace: 1
evidence model calls: 2
accepted evidence: 3
discarded candidates: 10
claim generation attempt 1: 1 call
verification attempt 1: 3 errors
business revision: 1 call
verification attempt 2: passed
finalize: completed
physical model calls: 4
total tokens: 3682
```

本次没有观察到结构化解析重试或 Transport Retry；第四次模型调用来自业务修订。

## 当前 Agent 性体现在哪里

- 根据 Goal 创建受约束 Plan；
- 根据 Plan dependencies 决定步骤可执行性；
- 通过 Registry 选择和调用工具；
- 根据验证结果决定是否重入部分步骤；
- 根据证据门禁决定是否定向重提取问题来源；
- 能在 Provider 失败时按策略重试或切换；
- 使用 Working Memory 传递 Evidence、Snapshot 和修订反馈；
- 所有决策进入 Trace。

## 当前仍偏工作流的部分

- RuntimePlanPolicy 固定六个步骤；
- 信息不足时不会动态插入 Evidence Retrieval；
- Evidence 格式和接受失败可触发一次修复，语义召回不足不会触发计划调整；
- 修订仍重建整个 Snapshot；
- 没有错误类型到修复动作的规则映射；
- 没有持久化 Checkpoint 和跨进程恢复。

准确表述应是：

> WorkPilot 是带受限 Planner、动态 DAG 调度、工具协议、验证门和有界修订的 evidence-grounded controlled Agent，而不是开放式无限 ReAct Agent。

## 下一步演进

1. 真实回归验证 Structured Repair 的成功率与额外成本；
2. 根据错误类型选择补检索、重定位、删 Claim 或展示冲突；
3. 保存 attempt 级 Diff 和 Checkpoint；
4. 评测证明收益后，再放宽动态步骤集合。
