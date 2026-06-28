# WorkPilot

Evidence-gated agent for project knowledge work.

## 安装

```bash
pip install -e ".[dev]"
```

## 使用

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
