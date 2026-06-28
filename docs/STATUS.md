# 项目状态

> **真值优先级**：代码+测试 > git log > 文档。本文件是导航，不是权威来源。

## 当前阶段

**Phase 1 — 骨架 + 最简 Loop** ✅ 已完成

Phase 2（Workspace Tools + Evidence 抽取）待启动。

## 最近变更

| Commit | 说明 |
|--------|------|
| `bd1c0a0` | 添加 smoke test 和测试 fixtures |
| `8aabee4` | Phase 1 核心实现：Runtime 状态机、Evidence Store、CLI、StubProvider |
| `cc9687d` | 设计文档：架构、数据模型、产品范围、开发方案 |
| `81d6748` | 项目初始化：pyproject.toml、.gitignore、README |

## 已知阻塞 / 待解决

- [ ] 无真实 LLM Provider（Phase 4 目标）
- [ ] Verifier 未实现（Phase 3 目标）
- [ ] Evidence 抽取依赖 StubProvider 固定返回，无智能筛选

## 下一步

1. Phase 2：实现 evidence extraction 逻辑，从 fixture workspace 抽取带 line range 的证据
2. Phase 3：实现 citation verifier，拒绝无效引用
