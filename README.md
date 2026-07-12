# WorkPilot

面向企业项目协作的证据驱动型 Agent。系统从项目文档、会议纪要和任务资料中提取可验证事实，生成带引用的周报、风险清单和行动项——每条结论可追溯，缺少证据时输出 unknown。

详细设计、开发计划和阅读顺序见 [文档阅读指南](./docs/00-文档阅读指南.md)。

## 安装

```bash
pip install -e ".[dev]"
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

## 模型供应商

```bash
workpilot providers
```

当前注册 Stub、OpenAI、Claude、DeepSeek、Qwen/百炼、GLM、Gemini，以及任意 OpenAI-compatible 企业网关。
