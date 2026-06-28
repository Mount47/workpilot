# WorkPilot

证据门控的项目知识工作 Agent。给定项目资料，生成带引用的周报、风险清单和行动项——每条结论可追溯，缺证据时输出 unknown，不编造。

详细文档见 [docs/README.md](./docs/README.md)。

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
