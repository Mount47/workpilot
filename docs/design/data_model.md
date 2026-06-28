# 数据模型

这是第一版逻辑模型。MVP 可以用 SQLAlchemy + SQLite 实现。

## MissionContract

表示一次 run 的边界。

字段：

- `id`
- `run_id`
- `goal`
- `workspace_root`
- `allowed_tools`
- `max_steps`
- `token_budget`
- `time_budget_seconds`
- `allowed_artifact_types`
- `forbidden_actions`
- `created_at`

说明：

- `workspace_root` 应保存为绝对 normalized path。
- 工具执行时使用相对路径，并再次检查路径是否仍在 workspace 内。

## Run

表示一次完整 Agent 执行。

字段：

- `id`
- `status`：`pending`、`running`、`verifying`、`revision`、`passed`、`failed`、`cancelled`
- `goal`
- `workspace_root`
- `output_dir`
- `created_at`
- `started_at`
- `finished_at`
- `failure_reason`

## Source

表示本次 run 识别到的一个 workspace 文件。

字段：

- `id`
- `run_id`
- `source_id`
- `relative_path`
- `absolute_path`
- `content_hash`
- `media_type`
- `source_kind`：`meeting_notes`、`issue_list`、`pr_summary`、`commit_summary`、`requirement_change`、`project_doc`、`unknown`
- `line_count`
- `created_at`

`source_id` 是 evidence 和 artifact 引用使用的稳定 ID，例如 `SRC-0001`。

## Evidence

表示可被 artifact 引用的来源片段。

字段：

- `id`
- `run_id`
- `evidence_id`
- `source_id`
- `file_path`
- `quote`
- `start_line`
- `end_line`
- `char_start`
- `char_end`
- `evidence_type`：`progress`、`decision`、`risk`、`action_item`、`blocker`、`requirement_change`、`context`、`unknown`
- `extracted_by_step`
- `created_at`

规则：

- `quote` 必须能在 source file 中找到。
- 对纯文本来源，优先记录 `start_line` 和 `end_line`。
- Artifact 只能引用此表中的 `evidence_id`。

## Plan

表示一次 run 的计划步骤。

字段：

- `id`
- `run_id`
- `version`
- `status`
- `steps_json`
- `created_by`
- `created_at`

每个 plan step 应包含：

- `step_id`
- `type`
- `purpose`
- `tool`
- `inputs`
- `expected_outputs`
- `status`

## Artifact

表示生成的交付物。

字段：

- `id`
- `run_id`
- `artifact_type`：`weekly_report`、`risks`、`action_items`、`verification_report`、`trace`
- `relative_path`
- `content_hash`
- `schema_version`
- `created_at`

## RiskItem

可以单独持久化，也可以嵌在 `risks.json` 中。单独持久化有利于后续查询。

字段：

- `id`
- `run_id`
- `risk_id`
- `title`
- `description`
- `severity`：`low`、`medium`、`high`、`unknown`
- `status`：`open`、`monitoring`、`mitigated`、`unknown`
- `source_refs`
- `reason`

规则：

- `source_refs` 必须是 evidence IDs。
- 风险必须引用含有阻塞、延迟、冲突、失败或需求变更信号的 evidence。

## ActionItem

可以单独持久化，也可以嵌在 `action_items.json` 中。

字段：

- `id`
- `run_id`
- `action_id`
- `title`
- `owner`
- `owner_status`：`known`、`unknown`
- `due_date`
- `due_date_status`：`known`、`unknown`
- `source_refs`
- `reason`
- `status`：`proposed`、`confirmed`、`blocked`、`unknown`

规则：

- 如果 `owner` 缺失，`owner_status` 必须是 `unknown`。
- 如果 `due_date` 缺失，`due_date_status` 必须是 `unknown`。
- unknown 是允许的，编造值不允许。

## VerificationReport

表示 verifier 输出。

字段：

- `id`
- `run_id`
- `status`：`passed`、`failed`
- `citation_checks`
- `support_checks`
- `schema_checks`
- `contract_checks`
- `trace_checks`
- `created_at`

每条 check result 应包含：

- `check_id`
- `status`
- `severity`
- `artifact`
- `location`
- `message`
- `source_refs`

## TraceEvent

表示一条 runtime journal event。

字段：

- `id`
- `run_id`
- `sequence`
- `event_type`
- `step_id`
- `tool_name`
- `input_summary`
- `output_summary`
- `status`
- `error`
- `created_at`

Trace 应为 append-only。

