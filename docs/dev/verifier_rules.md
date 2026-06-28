# Verifier 规则

Verifier 是 WorkPilot 的护城河。它把项目从普通总结器变成 evidence-gated agent。

## 原则

- Verifier checks 必须具体、可复现。
- MVP 必须包含确定性检查。
- 后续可以加入 LLM 辅助检查，但不能替代确定性 citation 和 schema 检查。
- Verification errors 应尽可能指向 artifact 的具体位置。
- Verification 失败后，应产出可用于有限修正 pass 的修复提示。

## Citation Verifier

目的：确认所有引用都指向真实 evidence 和真实 source text。

检查：

- Artifacts 中每个 `source_ref` 都存在对应 `evidence_id`。
- 每条被引用 evidence 都有有效 `source_id`。
- Evidence 的 source file 存在于 workspace 内。
- Evidence 的 `quote` 能在 source file 中找到。
- 如果记录了 line range，quote 必须出现在这些行内。
- Source file 当前内容 hash 与 retrieval 时记录的 hash 一致；若不一致，报告 source changed。
- Artifact 不得引用 evidence store 中不存在的未知来源。

失败示例：

- `source_ref E-0012 does not exist`
- `quote for E-0007 cannot be found in src/meeting.md lines 12-18`
- `artifact cites ../outside/file.md, which is outside workspace`

## Support Verifier

目的：确认每条关键结论都有合适证据支撑。

MVP 的 support verifier 不需要完美证明语义真伪，但必须强制 claim-to-evidence discipline 和 source-type constraints。

### Claim 类型

Weekly report claims：

- 进展
- 决策
- 变更
- 风险
- 阻塞
- 下一步

Risk item claims：

- 风险陈述
- 原因或证据说明
- 严重程度，如果有
- 状态，如果有

Action item claims：

- 任务标题
- 原因
- 负责人，如果已知
- 截止日期，如果已知
- 状态

### Source-Type 规则

- 进展必须引用 issue、PR、commit 或 meeting evidence。
- 决策必须引用会议纪要或需求变更记录。
- 需求变更必须引用需求变更记录或会议决策。
- 风险必须引用包含 blocker、delay、dependency、conflict、failure、regression、unknown scope、missed deadline、requirement churn 等信号的 evidence。
- 行动项必须引用会议决定、issue、明确任务记录或风险 evidence。

### Evidence-Type 规则

Evidence record 应携带 `evidence_type`。Support verifier 检查被引用 evidence type 是否适用于该 claim。

示例映射：

| Claim | 允许的 evidence types |
| --- | --- |
| Progress | `progress`、`context`、`decision` |
| Decision | `decision`、`requirement_change` |
| Risk | `risk`、`blocker`、`requirement_change` |
| Action item | `action_item`、`decision`、`risk`、`blocker` |

### 必需 Source References

- Weekly report 中每条事实性项目结论 bullet 至少包含一个 source reference。
- 每条 risk 至少包含一个 source reference。
- 每条 action item 至少包含一个 source reference。
- 汇总标题和连接性文字不要求引用。

### Unknown 处理

Verifier 必须拒绝编造：

- 负责人
- 截止日期
- 严重程度
- 状态

如果来源证据缺失，字段必须使用 `unknown` 状态。例如：

```json
{
  "owner": null,
  "owner_status": "unknown",
  "due_date": null,
  "due_date_status": "unknown"
}
```

## Task Schema Verifier

目的：确保 `action_items.json` 可作为结构化任务草稿使用。

必需字段：

- `action_id`
- `title`
- `owner`
- `owner_status`
- `due_date`
- `due_date_status`
- `source_refs`
- `reason`
- `status`

规则：

- `title` 必须非空。
- `owner_status` 必须是 `known` 或 `unknown`。
- 如果 `owner_status=known`，`owner` 必须非空。
- 如果 `owner_status=unknown`，`owner` 必须为 null 或空。
- `due_date_status` 必须是 `known` 或 `unknown`。
- 如果 `due_date_status=known`，`due_date` 必须是 ISO date format。
- 如果 `due_date_status=unknown`，`due_date` 必须为 null 或空。
- `source_refs` 必须是非空列表，且全部为有效 evidence IDs。
- `reason` 必须非空，并引用相同或相关 evidence。
- `status` 必须是 `proposed`、`confirmed`、`blocked`、`unknown` 之一。

## Risk Schema Verifier

必需字段：

- `risk_id`
- `title`
- `description`
- `severity`
- `status`
- `source_refs`
- `reason`

规则：

- `severity` 必须是 `low`、`medium`、`high`、`unknown`。
- `status` 必须是 `open`、`monitoring`、`mitigated`、`unknown`。
- `source_refs` 必须是非空列表，且全部为有效 evidence IDs。
- 风险 evidence 必须体现负面或不确定信号。

## Weekly Report Verifier

MVP Markdown citation format：

```text
Completed payment retry design review. [E-0003]
```

检查：

- Citation tokens 能匹配 evidence IDs。
- 事实性 bullet 至少包含一个 citation。
- 报告包含必需章节：
  - `## Summary`
  - `## Progress`
  - `## Decisions`
  - `## Risks`
  - `## Next Week`
- 缺失信息必须明确标记 unknown。

## Contract Verifier

目的：确认 run 没有越界。

检查：

- Trace event 未使用 forbidden tool。
- 文件访问未越过 workspace。
- step count 未超过 `max_steps`。
- Required artifacts 属于 allowed artifact types。
- 没有尝试 external actions。
- revision pass count 未超过配置。

## Trace Verifier

目的：确认 run 可审计。

检查：

- Trace 包含 contract creation。
- Trace 包含 plan creation。
- Trace 记录 scanned files。
- Trace 记录 evidence extraction。
- Trace 记录 synthesis。
- Trace 记录 verification。
- Trace 记录 final status。
- 每个 artifact 都有相关 trace event。

## Verification Report 形状

```json
{
  "status": "failed",
  "checks": [
    {
      "check_id": "citation.exists",
      "status": "failed",
      "severity": "error",
      "artifact": "weekly_report.md",
      "location": "line 18",
      "message": "Evidence ref E-0009 does not exist",
      "source_refs": ["E-0009"]
    }
  ]
}
```

