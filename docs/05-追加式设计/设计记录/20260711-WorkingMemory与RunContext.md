# 追加项目点设计记录：Working Memory 与 RunContext

## 元信息

- 状态：已完成
- 创建日期：2026-07-11
- 最后更新：2026-07-11
- 关联模块：Runtime、Memory、Evidence、ProjectSnapshot、Verification、Artifact、Budget
- 关联问题：单次 Run 中间状态分散；Planner 缺少统一上下文

## 问题与动机

当前 Runtime 通过局部变量和多个独立对象保存文件、Evidence、ProjectSnapshot、验证反馈、Artifact 与预算。状态可以运行，但缺少统一的 RunContext：后续 Planner 无法稳定读取当前上下文，失败恢复也无法判断哪些步骤和数据已经完成。

同时，不能把当前 Run 的全部临时数据直接称为长期记忆。未经验证的 Claim、修订中间稿和模型错误如果被长期保存，会造成记忆污染。

## 用户场景

一次 Run 执行过程中，系统应能统一回答：当前在哪个步骤、已发现哪些 Evidence、当前 Snapshot 是哪一版、验证失败原因是什么、已经生成哪些 Artifact、发生了几次修订、预算剩余多少。无论成功或失败，都应输出一份不包含原文全文的安全 RunContext 快照。

## 产品边界判断

Working Memory 是动态 Planner、长期 Memory 和运行恢复的共同前置能力，属于 Agent 后端核心状态管理，不引入外部数据源或高风险动作。

## 当前实现

- Runtime 局部变量保存 feedback、artifacts 和 verify_results；
- EvidenceStore 独立存在；
- project_snapshot 保存在 Runtime 属性；
- ExecutionBudget 独立保存资源状态；
- Trace 记录事件，但不是可查询的当前状态模型；
- 运行结束没有 RunContext Artifact。

## 目标与非目标

### 目标

- 新增 WorkingMemory，作为单次 Run 的状态聚合器；
- 管理步骤生命周期、EvidenceStore、ProjectSnapshot、验证摘要、修订反馈、Artifact 名称和 Budget Snapshot；
- 保持 Runtime.evidence_store 兼容访问；
- 成功和失败都输出 `run_context.json`；
- 快照只包含 ID、计数、状态和安全摘要，不复制 Evidence quote、Prompt 或 Artifact 全文；
- 为后续 Planner 提供稳定读取接口；
- 增加生命周期和 Runtime 集成测试。

### 非目标

- 不实现项目长期记忆；
- 不实现经验记忆；
- 不引入数据库；
- 不支持跨进程恢复；
- 不把 run_context.json 当作可直接恢复的完整检查点；
- 不保存完整 Prompt、模型响应或来源正文。

## 备选方案

### 方案一：继续从 Trace 重建状态

Trace 适合审计事件，但重建当前状态需要重放全部事件，业务代码读取复杂，且无法直接维护运行时对象引用。

### 方案二：WorkingMemory 维护当前状态，Trace 记录状态变化

存在少量状态重复，但职责清晰：Memory 回答“现在是什么”，Trace 回答“如何变成这样”。

### 保持现状

Planner 会直接依赖 Runtime 局部变量，模块边界继续恶化。

## 最终决策及原因

采用方案二。WorkingMemory 拥有 EvidenceStore 并聚合当前状态；Runtime 负责触发状态变更；Trace 保持追加式审计。Memory 不直接调用 Provider、工具或数据库。

## 模块与依赖影响

- 新增 `memory/working.py` 与公共导出；
- Runtime 初始化 WorkingMemory 并通过它注册 Evidence、Snapshot、验证与 Artifact；
- Runtime Step Context 同步步骤状态；
- ExecutionBudget 每次变化后同步安全快照；
- Mission Contract 增加 run_context Artifact 类型；
- Smoke 与预算失败测试增加 RunContext 断言。

## 数据和接口变更

RunContext 安全快照包含：

```text
run_id
goal
run_status
contract_summary
steps[]
evidence_ids[]
evidence_count
project_snapshot_id
claim_ids[]
verification_summary
revision_count
artifact_names[]
budget
```

不包含 Evidence quote、来源全文、Prompt、模型响应和 Artifact 内容。

## 安全、权限和隐私

- Contract 只导出允许工具、预算和 workspace 路径，不导出密钥；
- 验证错误只保存 check_id、severity 和 location；
- Artifact 只保存文件名；
- Memory 不跨 Project 或 Run 共享；
- Snapshot 输出使用 JSON-safe 数据并保持最小化。

## 失败、重试与恢复

- Step 开始、完成和失败都更新 Memory；
- 修订反馈只在当前 Run 内保存；
- Runtime 顶层失败时更新 run_status 和最后错误类型；
- finally 同步最终预算并写 run_context.json；
- 当前快照用于调试和未来恢复设计输入，不承诺直接恢复。

## 可观测性

Memory 状态变化继续由 Runtime 对应 Trace Step 和业务事件解释，不单独记录包含敏感内容的 memory dump 事件。

## 实施阶段

1. Memory 模型与生命周期；
2. Runtime Evidence 与 Step 接入；
3. Snapshot、Verification、Artifact、Budget 接入；
4. 安全快照输出；
5. 测试和文档同步。

## 测试与评测

- Step 合法生命周期和重复 ID；
- Evidence 注册与安全快照不包含 quote；
- Snapshot、验证和 Artifact 状态；
- 成功 RunContext；
- 步骤预算和 Token 预算失败 RunContext；
- 全量测试和最小 Eval 回归。

## 验收标准

- Runtime 的核心中间状态通过 WorkingMemory 管理；
- run_context.json 在成功和失败时都存在；
- 快照不包含 Evidence quote 或完整 Artifact；
- Step、Evidence、Claim、验证、修订和预算状态准确；
- 全量测试与最小评测通过；
- 文档状态与代码一致。

## 实际实施结果

- 新增 WorkingMemory、MemoryStep、VerificationMemory 和安全 WorkingMemorySnapshot；
- WorkingMemory 持有 Run 的 EvidenceStore，并保持 Runtime.evidence_store 兼容访问；
- Runtime 步骤开始、完成和失败会同步 Memory；
- Evidence、ProjectSnapshot、验证摘要、修订反馈、Artifact 名称和 Budget Snapshot 已接入；
- 成功、步骤预算失败和 Token 预算失败都会输出 `run_context.json`；
- 安全快照只包含 ID、计数、状态、资源和错误分类，不包含 Evidence quote、Claim 文本、Prompt、修订反馈正文或 Artifact 全文；
- 新增 Working Memory 生命周期、安全导出和 Runtime 集成测试；
- 全量测试 50 passed，总体覆盖率约 84%；最小 Stub 评测继续通过。

## 遗留问题与后续项目点

- Working Memory 仅存在于当前进程，run_context.json 不是完整恢复检查点；
- Step 状态由 Runtime 主动同步，尚未使用统一状态事件总线；
- Artifact 内容仍由 Runtime 局部变量暂存，Memory 只记录安全元数据；
- 没有容量限制、摘要或淘汰策略，因为当前 Memory 不保存大段正文；
- 项目长期记忆、过期、冲突和版本尚未实现；
- 下一阶段应实现结构化 Planner 与 Plan Executor，直接读取 Working Memory 的安全状态接口。
