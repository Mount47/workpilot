# 接口规划

## 概述

WorkPilot 采用 CLI 优先策略，Runtime 稳定后加 FastAPI 层。CLI 和 API 共享同一套 runtime，不重复实现。

## CLI

### 执行 Mission

```bash
workpilot run \
  --workspace ./workspace/project-a \
  --goal "根据这些资料，生成本周项目周报、风险清单和下周行动项。" \
  --provider openai \
  --model gpt-4o \
  --output ./runs/project-a-2026-06-25 \
  --max-steps 30 \
  --time-budget-seconds 300
```

输出：

```text
runs/project-a-2026-06-25/
  weekly_report.md
  risks.json
  action_items.json
  verification_report.json
  trace.json
```

### 验证已有 Artifacts

```bash
workpilot verify \
  --workspace ./workspace/project-a \
  --run ./runs/project-a-2026-06-25
```

### 查看 Evidence

```bash
workpilot evidence list --run ./runs/project-a-2026-06-25
workpilot evidence show E-0007 --run ./runs/project-a-2026-06-25
```

### 查看 Trace

```bash
workpilot trace show --run ./runs/project-a-2026-06-25
```

## API（Phase 8+）

### 创建 Run

```http
POST /runs
```

```json
{
  "workspace_root": "/abs/path/to/workspace",
  "goal": "根据这些资料，生成本周项目周报、风险清单和下周行动项。",
  "provider": "openai",
  "model": "gpt-4o",
  "max_steps": 30,
  "time_budget_seconds": 300,
  "allowed_artifact_types": [
    "weekly_report", "risks", "action_items",
    "verification_report", "trace"
  ]
}
```

### 获取 Run

```http
GET /runs/{run_id}
```

### 获取 Artifact / Evidence / Verification / Trace

```http
GET /runs/{run_id}/artifacts/{artifact_name}
GET /runs/{run_id}/evidence
GET /runs/{run_id}/evidence/{evidence_id}
GET /runs/{run_id}/verification
GET /runs/{run_id}/trace
```

## Artifact Schemas

### `risks.json`

```json
{
  "schema_version": "0.1",
  "risks": [
    {
      "risk_id": "R-0001",
      "title": "Payment retry behavior remains unresolved",
      "description": "The retry behavior is still blocked by an unresolved API decision.",
      "severity": "medium",
      "status": "open",
      "source_refs": ["E-0004"],
      "reason": "Meeting notes identify the retry API decision as unresolved."
    }
  ]
}
```

### `action_items.json`

```json
{
  "schema_version": "0.1",
  "action_items": [
    {
      "action_id": "A-0001",
      "title": "Confirm payment retry API behavior",
      "owner": null,
      "owner_status": "unknown",
      "due_date": null,
      "due_date_status": "unknown",
      "source_refs": ["E-0004"],
      "reason": "The unresolved API decision blocks payment retry work.",
      "status": "proposed"
    }
  ]
}
```
