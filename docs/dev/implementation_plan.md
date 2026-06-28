# 开发方案

## 概述

五阶段递进开发，每阶段交付可独立测试的增量。验证器优先于 LLM 生成功能。

## Phase 0 — 方案确认（已完成）

交付物：架构文档、数据模型、接口设计、Provider 架构、本方案。

## Phase 1 — 骨架 + 最简 Loop

**目标**：跑通"给文件 → 读 → 输出"完整链路，不依赖 LLM。

交付物：
- `pyproject.toml`、`.gitignore`、src layout
- CLI 入口（typer）：`workpilot run` / `workpilot trace show`
- Runtime 状态机骨架
- MissionContract dataclass + 校验
- EvidenceStore CRUD
- TraceJournal append-only
- StubProvider（返回固定 fixture 数据）
- 1 个 smoke test

退出标准：
- `workpilot --help` 可运行
- `workpilot run --provider stub` 跑通全链路
- smoke test 通过，无网络依赖

## Phase 2 — Workspace Tools + Evidence 抽取

**目标**：能扫描真实 workspace，抽取 evidence。

新增文件：
- `workspace/tools.py`：list_files、read_file、search_text
- `evidence/extraction.py`：证据抽取逻辑

测试：
- 拒绝 path traversal
- 拒绝 workspace 外文件
- 从 fixture workspace 抽取 evidence
- Evidence quote 存在于 source file

退出标准：
- 能扫描 fixture workspace 并记录 trace events
- evidence IDs 在单次 run 内稳定

## Phase 3 — 确定性 Verifier（引用存在性）

**目标**：引用检查作为第一个验证维度上线。

新增文件：
- `verification/citation_verifier.py`
- `verification/base.py`：Verifier 抽象基类

测试：
- 缺失 evidence ID → 失败
- 错误 quote → 失败
- 正确引用 → 通过
- source file hash 变化 → 报 source changed

退出标准：
- Verifier 能拒绝坏 artifacts，通过正确 fixture artifacts

## Phase 4 — LLM Provider + 支撑验证（LLM-Judge）

**目标**：接通真实 LLM，加 Support Verifier。

新增文件：
- `providers/openai_provider.py`（同时服务 DeepSeek / Qwen / GLM / 百炼）
- `providers/registry.py`
- `verification/support_verifier.py`

新增能力：
- OpenAI structured output 接入
- claim type → evidence type 映射检查
- LLM 辅助判断 claim 是否被 evidence 支撑

测试：
- Provider mock 单元测试
- Support verifier 单元测试
- Malformed response 安全失败

退出标准：
- 真实 provider 能在样例文档上运行
- Verifier 仍是最终权威，LLM 只辅助判断

## Phase 5 — 评测 + README + Claude Provider

**目标**：完善文档、加 Claude 支持、端到端评测。

新增/改动：
- `providers/anthropic_provider.py`
- `tests/test_e2e_full_pipeline.py`
- `README.md`
- 完善 `docs/interface/implementation.md`

测试：
- 端到端测试：workspace → artifacts → verification
- 多 provider 切换测试

退出标准：
- 端到端测试通过
- README 包含安装、使用、架构说明
- CLI 和 API 共享同一套 runtime

---

## 实现优先级原则

```
1. Boundary 和 trace（Runtime 约束）
2. Evidence Store（可信数据源）
3. Stubbed artifact generation（测试闭环）
4. Deterministic verifier（护城河）
5. Real LLM（质量提升）
```

Evidence gate 必须先存在，再让流畅生成成为重点。
