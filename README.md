# WorkPilot

面向企业项目协作的证据驱动型 Agent。系统从项目文档、会议纪要和任务资料中提取可验证事实，生成带引用的周报、风险清单和行动项——每条结论可追溯，缺少证据时输出 unknown。

详细设计、开发计划和阅读顺序见 [文档阅读指南](./docs/00-文档阅读指南.md)。

## 安装

```bash
pip install -e ".[dev]"
```

启用持久化 Web 服务时，安装 Web 与 PostgreSQL 可选依赖：

```bash
pip install -e ".[dev,web,database]"
```

## 快速使用

```bash
workpilot run \
  --workspace ./tests/fixtures/workspaces/basic_project \
  --goal "生成本周项目周报" \
  --provider stub \
  --output ./runs/test-run
```

## 测试

```bash
pytest
```

## 最小评测

```bash
workpilot eval \
  --suite ./evals/最小基线.json \
  --provider stub \
  --output ./runs/eval-baseline
```

Planner 安全与降级策略可以离线评测：

```bash
workpilot planner-eval \
  --suite ./evals/Planner最小基线.json \
  --output ./runs/planner-eval-baseline
```

## 模型供应商

```bash
workpilot providers
```

当前注册 Stub、OpenAI、Claude、DeepSeek、Qwen/百炼、GLM、Gemini，以及任意 OpenAI-compatible 企业网关。

真实模型调用前，复制配置模板并只填写准备使用的 Provider Key：

```bash
cp .env.example .env
workpilot doctor --provider deepseek
```

Doctor 完全离线运行，只显示 Key 是否已配置，不显示 Key 内容。返回 `Overall ready: yes` 后，可以先在脱敏样例上做一次低预算真实运行：

```bash
workpilot run \
  --workspace ./tests/fixtures/workspaces/basic_project \
  --goal "生成本周项目周报" \
  --provider deepseek \
  --token-budget 10000 \
  --output ./runs/real-model-smoke
```

真实 API Key 不要通过命令行参数、路由文件、聊天消息或 Git 提交传递。

## 分层模型路由

```bash
workpilot run \
  --workspace ./tests/fixtures/workspaces/basic_project \
  --goal "生成本周项目周报" \
  --route-config ./examples/model_routes.json \
  --output ./runs/routed-run
```

示例按 Evidence 抽取、分析、修订、规划和语义复核配置不同模型档位。配置 `planning` Route 后，Runtime 会尝试受限 LLM Planner；非法计划或可降级 Provider 失败会回退 DeterministicPlanner。API Key 只从环境变量或 `.env` 读取，不应写入路由文件。

启用路由前先运行：

```bash
workpilot doctor --route-config ./examples/model_routes.json
```

## Web 服务与前端

除 CLI 外,可以通过 HTTP 服务触发运行并在浏览器中查看可追溯报告。

持久化服务需要先在 `.env` 配置 `WORKPILOT_DATABASE_URL`，再显式执行数据库迁移：

```bash
alembic upgrade head
```

启动后端（默认绑定 127.0.0.1）：

```bash
workpilot serve --workspace-root ./tests/fixtures/workspaces
```

只有本地临时开发允许显式使用内存 Repository；该模式重启会丢失 Run 历史：

```bash
workpilot serve \
  --workspace-root ./tests/fixtures/workspaces \
  --in-memory-runs
```

`--workspace-root` 是唯一允许被分析的目录,其子目录之外的路径会被拒绝。服务当前不带鉴权,仅靠 localhost 绑定与工作区白名单兜底,请勿在未加认证的情况下绑定到 `0.0.0.0` 或公网地址。

启动前端(开发态,另开一个终端):

```bash
cd web
npm install
npm run dev
```

在报告中点击 `[E-xxxx]` 引用标记,即可打开原文并高亮被引用的行——这是本项目"每条结论可追溯到原文证据"的核心交互。

主要端点:`POST /api/runs` 触发运行,`GET /api/runs/{id}` 轮询状态,`GET /api/runs/{id}/report`、`.../artifacts/{artifact}`、`.../source/{source_id}` 读取报告、产物与源文件。

> 说明：配置 PostgreSQL 后，Run、PlanStep、ToolCall、Artifact Metadata 和 Checkpoint Metadata 由 Repository 持久化；`Idempotency-Key` 可防止同一 API 请求重复创建 Run。Runtime 会在每个已提交步骤后原子写入版本化 Checkpoint，并通过数据库时间租约、单调 `execution_attempt` fencing 和 Heartbeat 阻止旧 Worker 写入。内部 Restorer 已能校验并重建执行状态，但启动恢复扫描、公开恢复入口、Provider 调用去重、鉴权和可靠任务队列仍未实现，因此不能直接作为公网高可用服务。详见 [下一阶段改进路线图](./docs/02-项目开发计划/09-下一阶段改进路线图.md)。
