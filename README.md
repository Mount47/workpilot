# WorkPilot

面向企业项目协作的证据驱动型 Agent。系统从项目文档、会议纪要和任务资料中提取可验证事实，生成带引用的周报、风险清单和行动项——每条结论可追溯，缺少证据时输出 unknown。

详细设计、开发计划和阅读顺序见 [文档阅读指南](./docs/00-文档阅读指南.md)。

## 安装

```bash
pip install -e ".[dev]"
```

启用 Web 服务与前端时,额外安装 `web` 可选依赖:

```bash
pip install -e ".[dev,web]"
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

启动后端(默认绑定 127.0.0.1):

```bash
workpilot serve --workspace-root ./tests/fixtures/workspaces
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

> 说明:Run 注册表当前为内存态,服务重启会丢失历史;前后端为开发态双进程。持久化、鉴权与单进程部署见 [交接与难点](./docs/06-项目状态/04-交接与难点.md)。
