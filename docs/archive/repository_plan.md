# 仓库规划

第一版实现建议目录结构：

```text
workpilot/
  pyproject.toml
  README.md
  docs/
    README.md
    product_scope.md
    architecture.md
    data_model.md
    agent_runtime.md
    verifier_rules.md
    mvp_interface.md
    repository_plan.md
    development_plan.md
  src/
    workpilot/
      __init__.py
      cli.py
      config.py
      contracts/
        models.py
        service.py
      runtime/
        runner.py
        state.py
        steps.py
      planning/
        planner.py
        schemas.py
      workspace/
        paths.py
        scanner.py
        reader.py
        search.py
      evidence/
        store.py
        models.py
        extraction.py
      synthesis/
        synthesizer.py
        schemas.py
        prompts/
      verification/
        citation.py
        support.py
        schema.py
        contract.py
        trace.py
        report.py
      providers/
        base.py
        stub.py
        openai.py
      persistence/
        db.py
        models.py
        migrations/
      artifacts/
        writer.py
        schemas.py
      trace/
        journal.py
        models.py
  tests/
    fixtures/
      workspaces/
        basic_project/
    unit/
    integration/
```

## 边界

### `contracts`

Mission contract 模型和校验。这个包不应依赖 provider 代码。

### `runtime`

工作流编排。Runtime 拥有执行顺序和预算控制。

### `workspace`

安全文件系统访问。所有文件操作都应通过这个包，防止 path traversal。

### `evidence`

Evidence 持久化、抽取归一化和 evidence ID 分配。

### `synthesis`

基于 evidence 生成 artifacts。这个包可以调用 provider interfaces，但不应直接随意读取文件。

### `verification`

确定性 verifier 规则。这个包应有重点测试，并且不依赖真实 LLM provider。

### `providers`

LLM provider 抽象和具体实现。`stub.py` 是测试必需项。

### `trace`

Append-only run journal。

## 初始依赖方向

```text
cli -> runtime
runtime -> contracts, planning, workspace, evidence, synthesis, verification, trace, artifacts
synthesis -> providers, evidence
planning -> providers
verification -> evidence, artifacts, workspace, trace, contracts
workspace -> no project domain dependencies
providers -> no runtime dependencies
```

避免 runtime、provider、verifier 之间出现循环依赖。

