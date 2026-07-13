# DAG 调度与结果上下文实现

## 对应代码

- `src/workpilot/planning/scheduler.py`
- `src/workpilot/planning/executor.py`
- `src/workpilot/planning/models.py`
- `src/workpilot/runtime/runner.py`
- `tests/test_dag_scheduler.py`
- `tests/test_smoke.py`

## 当前实现

`SerialDAGScheduler` 已经接管 Runtime 初始六步主链路。调度顺序不再由 Runtime 逐行硬编码，而是由已验证 Plan 中的 dependencies 决定。

第一版只做串行调度。多个步骤同时 ready 时，按 `Plan.steps` 中的顺序执行，因此同一计划和相同输入具有稳定执行顺序，便于测试、追踪和失败复现。

## 调度循环

每一轮执行以下操作：

1. 把依赖 failed、blocked 或 skipped 的 pending 步骤标记为 blocked；
2. 找出全部依赖均 completed 的 pending 步骤；
3. 按计划顺序调用 Runtime 注入的受观测执行入口；
4. 成功结果写入 StepResultStore；
5. 工具失败时保留原始异常，继续执行独立分支；
6. 没有 ready 步骤但仍有 pending 步骤时抛出 SchedulerDeadlockError；
7. 全部步骤进入终态后返回 SchedulerRunResult。

Scheduler 捕获异常是为了完成失败传播，不会丢弃原始异常。Runtime 调用 `raise_first_failure()` 后仍能得到原来的 Budget、Provider 或工具错误类型。

## 状态语义

- `pending`：尚未满足执行条件；
- `running`：Handler 正在执行；
- `completed`：Handler 成功返回 ToolResult；
- `failed`：Handler 已执行但抛出异常；
- `blocked`：未执行，因为依赖已经进入失败终态；
- `skipped`：被显式调度策略跳过。

blocked 和 skipped 不增加 attempts，因为工具没有实际执行。

## StepResultStore

Store 使用 `step_id -> ToolResult` 保存同一个进程、同一次 Run 的步骤结果。下游 Handler 通过依赖步骤 ID 读取 output：

```text
scan_workspace.output
  -> extract_evidence

build_claims.output
  -> render_artifacts

render_artifacts.output
  -> verification.run / artifacts.finalize
```

`output` 可以包含内部对象，但不会出现在 `model_dump()`、Scheduler Trace 或 tool_call Trace 中。可观测数据只使用 `output_summary`、`evidence_ids` 和状态。

普通步骤禁止重复写入；受控重入使用 `allow_overwrite=True` 更新当前结果。每次 attempt 的历史由 Trace 保存，Store 当前只保留最新结果。

## 业务验证门

verification.run 成功执行不代表报告已经通过业务验证，因此不能把验证错误当成工具异常。

Runtime 第一次调度时使用：

```text
scheduler.run(stop_before_step_ids={"finalize"})
```

调度器执行到 verify 后暂停。Runtime 读取验证结果：

- 没有 error：构造验证报告并恢复 Scheduler，执行 finalize；
- 存在 error：生成反馈，只重入 claims.build、artifacts.render 和 verification.run；
- 达到三次上限：仍会持久化失败验证报告，最终 Run 标记为 failed。

该暂停点是 Runtime 的业务门控，不属于 Scheduler 的通用业务逻辑。

## Trace

新增事件：

- scheduler_started；
- scheduler_step_ready；
- scheduler_step_completed；
- scheduler_step_failed；
- scheduler_step_blocked；
- scheduler_step_skipped；
- scheduler_paused；
- scheduler_deadlocked；
- scheduler_completed。

正常 Run 会出现两次 scheduler_started：第一次执行到验证门并暂停，第二次从 finalize 恢复并完成。

## 已验证场景

- 菱形 DAG 稳定拓扑顺序；
- 一个分支失败、后继 blocked、独立分支继续；
- 显式 skipped 及依赖传播；
- 运行时循环导致的死锁；
- 业务门前暂停和后续恢复；
- 注入 Runtime 受观测执行边界；
- 结果覆盖保护与重入覆盖；
- output 不进入安全快照；
- Token 超预算原始异常不被 Scheduler 汇总覆盖；
- Stub 端到端运行产生完整 Scheduler Trace。

## 当前限制

- RuntimePlanPolicy 仍固定六个核心步骤和工具映射；
- 修订三步目前由 Runtime 显式受控重入，不是自动计划调整；
- success_criteria 仍是自然语言，尚未绑定确定性规则；
- Store 没有持久化，进程退出后不能恢复；
- 没有取消、超时、幂等键和并发执行。
