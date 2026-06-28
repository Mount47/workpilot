# MVP 接口

MVP 应先从 CLI 开始。等 Runtime 稳定后，再加一层小型 FastAPI 服务。

## CLI

### 执行 Mission

```bash
workpilot run \
  --workspace ./workspace/project-a \
  --goal "根据这些资料，生成本周项目周报、风险清单和下周行动项。" \
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

用途：用户手动修改 artifacts 后重新运行 verifier。

### 查看 Evidence

```bash
workpilot evidence list --run ./runs/project-a-2026-06-25
workpilot evidence show E-0007 --run ./runs/project-a-2026-06-25
```

### 查看 Trace

```bash
workpilot trace show --run ./runs/project-a-2026-06-25
```

## API

CLI 行为稳定后，FastAPI 可以暴露同一套 runtime。

### 创建 Run

```http
POST /runs
```

Request：

```json
{
  "workspace_root": "/abs/path/to/workspace",
  "goal": "根据这些资料，生成本周项目周报、风险清单和下周行动项。",
  "max_steps": 30,
  "time_budget_seconds": 300,
  "allowed_artifact_types": [
    "weekly_report",
    "risks",
    "action_items",
    "verification_report",
    "trace"
  ]
}
```

Response：

```json
{
  "run_id": "run_01",
  "status": "pending"
}
```

### 启动 Run

```http
POST /runs/{run_id}/start
```

Response：

```json
{
  "run_id": "run_01",
  "status": "running"
}
```

### 获取 Run

```http
GET /runs/{run_id}
```

Response：

```json
{
  "run_id": "run_01",
  "status": "passed",
  "output_dir": "/abs/path/to/runs/run_01",
  "artifacts": [
    "weekly_report.md",
    "risks.json",
    "action_items.json",
    "verification_report.json",
    "trace.json"
  ]
}
```

### 获取 Artifact

```http
GET /runs/{run_id}/artifacts/{artifact_name}
```

### 获取 Evidence

```http
GET /runs/{run_id}/evidence
GET /runs/{run_id}/evidence/{evidence_id}
```

### 获取 Verification Report

```http
GET /runs/{run_id}/verification
```

### 获取 Trace

```http
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

## MVP Provider 配置

环境变量可以后续再引入。第一版实现建议使用显式配置：

```bash
workpilot run --provider stub ...
workpilot run --provider openai ...
```

Stub provider 必须为测试提供确定性 fixture outputs。

