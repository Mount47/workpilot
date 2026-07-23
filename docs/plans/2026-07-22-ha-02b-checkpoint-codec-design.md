# HA-02B 版本化 Checkpoint Codec 设计

## 状态

Accepted for implementation。本文只覆盖 HA-02B：检查点格式、原子文件协议、数据库提交点与进程内恢复重建；租约接管和外部恢复入口分别留给 HA-02C、HA-02D。

## 问题

HA-02A 已持久化 Run、PlanStep、ToolCall 和 Artifact Metadata，但这些记录只有控制面状态。Runtime 的 `StepResultStore`、Evidence、ProjectSnapshot、验证结果与预算仍只存在于内存中。如果仅把已完成 Step 标记为 completed，进程重启后下游步骤仍拿不到上游输出，这会形成“台账看似可恢复、实际无法继续”的假能力。

## 方案比较

### 方案一：序列化整个 Python Runtime

使用 pickle 或同类机制保存对象图，开发成本最低。但反序列化存在代码执行风险，类结构变化会让旧快照不可读，且无法清晰审计保存了哪些敏感字段，因此拒绝。

### 方案二：崩溃后从头重跑

实现简单，也不需要保存中间状态。但模型与工具调用会重复执行和计费，无法满足一步级 RPO，也不属于检查点恢复，因此只适合作为显式人工降级，不作为 HA-02B 语义。

### 方案三：受限工具的显式 JSON Codec（采用）

只允许当前 Tool Registry 中六类固定工具写入检查点。每类结果由显式 Codec 转换为规范 JSON，并在读取时重新验证类型、Schema、Run 身份、文件大小与 SHA-256。该方案需要维护格式版本，但安全边界、迁移路径和故障行为最清晰。

## 核心不变量

1. 不允许 pickle，也不允许对任意 Python 对象做“尽力序列化”。未知工具或未知结果类型必须 fail closed。
2. completed PlanStep 必须关联一个数据库已提交且文件可校验的 Checkpoint。
3. 文件先在 Run 输出目录内原子写入，随后数据库在同一事务中登记 Checkpoint，并同时提交 ToolCall、PlanStep 与 Run 的最新序号。
4. 文件成功而数据库失败时产生的孤立文件不得用于恢复；数据库成功而文件缺失或损坏时恢复失败，不得猜测跳过步骤。
5. Checkpoint 文件可包含恢复必需的业务正文，必须继承 Run 目录权限；数据库只保存相对路径、摘要、大小和结构化控制面元数据。
6. Schema 版本、工具 Codec 版本和规范 JSON 编码规则必须显式固定。

## Checkpoint Payload v1

顶层字段：

```json
{
  "schema_version": 1,
  "run_id": "run_...",
  "sequence": 3,
  "committed_step_id": "claims.build",
  "plan": {},
  "step_results": {},
  "evidence": [],
  "runtime_state": {
    "project_snapshot": null,
    "rendered_artifacts": {},
    "verification_results": [],
    "verification_errors": [],
    "revision_attempt": 0,
    "revision_feedback": [],
    "budget": {}
  }
}
```

`step_results` 以 Step ID 为键，每项包含 `tool`、`codec_version` 和 `value`。Codec Registry 只接受以下工具：

| 工具 | v1 恢复值 |
|---|---|
| `workspace.scan` | 规范化相对路径列表 |
| `evidence.extract` | 提取摘要；Evidence 正文由顶层 `evidence` 重建 |
| `claims.build` | `ProjectSnapshot` 的显式字段模型 |
| `artifacts.render` | 已渲染产物映射 |
| `verification.run` | `VerifyResult` 与错误结果的列表 |
| `artifacts.finalize` | JSON `null` |

Plan 使用 Pydantic 的 JSON 模式输出，但恢复时仍经过 `Plan.model_validate` 和现有 `PlanValidator`。所有字典键排序，UTF-8、无 BOM、紧凑分隔符，保证相同 Payload 产生相同摘要。

## 文件协议

- 相对路径：`checkpoints/checkpoint-{sequence:06d}.json`。
- 在 `checkpoints` 目录内创建随机临时文件；写入后 flush + fsync，再以 `os.replace` 原子替换最终路径。
- 最终文件写入后重新读取字节，计算 SHA-256 与大小，并立即按 v1 Schema 解码校验。
- 路径解析后必须仍位于该 Run 的输出目录内，禁止绝对路径和 `..`。
- 读取时同时校验数据库元数据中的路径、大小、摘要、Schema 版本、Run ID 和序号。

## 数据库模型

新增 `checkpoints` 表：

- `run_id`、`sequence`：复合唯一身份；
- `step_id`：本次提交完成的 Step；
- `schema_version`；
- `relative_path`、`sha256`、`byte_size`；
- `created_at`。

`runs` 增加非空 `checkpoint_sequence`，默认 0。`plan_steps` 增加可空 `checkpoint_sequence`。数据库事务必须：

1. 锁定/读取 ToolCall、PlanStep、Run；
2. 校验 sequence 等于 `runs.checkpoint_sequence + 1`；
3. 插入 Checkpoint Metadata；
4. 把 ToolCall 与 PlanStep 标记 completed，并让 PlanStep 指向该 sequence；
5. 更新 Run 的 `checkpoint_sequence` 和版本；
6. 一次提交。

重复提交相同元数据可幂等返回；相同 `(run_id, sequence)` 或 Step 对应不同摘要时必须抛出稳定冲突错误。

## Runtime 协调

Executor 在工具成功并通过 Success Rule 后，把结果放入候选内存状态。Checkpoint Coordinator 取得 Plan、StepResultStore、EvidenceStore、ProjectSnapshot、验证结果、修订状态和预算的不可变快照，编码并原子写文件。文件校验成功后，Observer 调用 Repository 的带 Checkpoint 完成事务。只有该事务成功，Scheduler 才允许依赖步骤运行。

如果文件或数据库提交失败，当前步骤不视为 committed。内存中的候选结果可以丢弃，恢复时该步骤按 at-least-once 语义重新执行。

## 恢复重建

HA-02B 提供内部 `CheckpointRestorer`，但暂不提供自动扫描或 API/CLI 接管入口。Restorer：

1. 从 Repository 读取最新已提交元数据；
2. 通过 Checkpoint Store 完整校验文件；
3. 验证 Plan 与当前受限 Tool Registry 兼容；
4. 重建 EvidenceStore、StepResultStore、ProjectSnapshot、VerifyResult、预算和修订状态；
5. completed Step 保持 completed；不在 Checkpoint 中的 running Step 回到 pending；
6. 返回可由 HA-02D 注入 Runtime 的恢复状态。

未知 Schema、未知 Codec、类型错误、路径越界、摘要不一致、缺失文件、Run/sequence 不一致均抛出稳定的 `CheckpointValidationError`。

## 安全与数据保留

检查点可能含源文档片段、结论和中间产物，不写入 Trace，也不进入数据库 JSON 字段。当前版本依赖 Run 输出目录的文件权限；字段级加密、密钥轮换、保留期和安全擦除属于 HA-03 的 Trace 脱敏与密钥管理范围。文档和产品状态必须明确这一限制。

## 验收标准

- 六个受限工具的结果均可 JSON round-trip，未知类型被拒绝；
- 相同状态产生字节级一致的规范 JSON；
- 路径越界、损坏、截断、摘要/大小/身份不一致均 fail closed；
- ToolCall、PlanStep、Checkpoint 与 Run sequence 原子提交；
- 每个步骤后注入崩溃，恢复状态与最后一次数据库提交一致；
- 现有 Evidence、Verifier、Bad Case、API 和前端回归不退化；
- PostgreSQL 迁移可升级、降级、再升级，并保留 HA-02A 数据。

