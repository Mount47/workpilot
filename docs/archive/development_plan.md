# 开发计划

## Phase 0：设计基线

状态：当前文档阶段。

交付物：

- 产品范围
- 整体架构
- 数据模型
- Runtime 流程
- Verifier 规则
- CLI/API 设计
- 仓库规划

退出标准：

- 项目有清晰 MVP 主脊。
- 非目标明确。
- Verifier 行为具体到可以实现。

## Phase 1：项目骨架

交付物：

- `src/workpilot` 下的 Python package layout。
- `pyproject.toml`。
- CLI entrypoint。
- 基础 config model。
- 测试配置。

退出标准：

- `workpilot --help` 可运行。
- 单元测试 runner 可运行。
- 不依赖 LLM。

## Phase 2：Mission Contract 和 Workspace Tools

交付物：

- Mission contract model。
- 安全 path normalization。
- Workspace file listing。
- 按相对路径读取文件。
- 文本搜索。
- Trace event model。

测试：

- 拒绝 path traversal。
- 拒绝 workspace 外文件。
- 强制 allowed tools。
- 强制 max step count。

退出标准：

- Run 能扫描 fixture workspace 并记录 trace events。

## Phase 3：Evidence Store

交付物：

- Source indexing。
- Evidence model 和 ID generation。
- Evidence insertion。
- Quote 和 line range validation。
- Evidence listing CLI。

测试：

- Evidence quote 存在于 source。
- Invalid quote 失败。
- Evidence IDs 在单次 run 内稳定。
- 能检测 source hash 变化。

退出标准：

- Fixture project evidence 能在无 LLM 情况下注册并验证。

## Phase 4：Stub Planner 和 Synthesizer

交付物：

- Provider base interface。
- Stub provider。
- Structured plan schema。
- Artifact schemas。
- 基于 fixture evidence 生成 stub weekly report、risks、action items。

测试：

- Runtime 能用 stub provider 生成所有 MVP artifacts。
- Artifact source refs 指向 evidence store IDs。

退出标准：

- End-to-end CLI run 能在 fixture workspace 上无网络运行。

## Phase 5：确定性 Verifier

交付物：

- Citation verifier。
- Risk schema verifier。
- Action item schema verifier。
- Weekly report citation verifier。
- Contract verifier。
- Trace verifier。
- `verification_report.json` writer。

测试：

- 缺失 evidence ID 会失败。
- 错误 quote 会失败。
- 缺少 action item owner status 会失败。
- 正确表达 unknown owner/date 会通过。
- Unsupported risk 会失败。
- Contract violation 会失败。

退出标准：

- Verifier 能拒绝坏 artifacts，并通过正确 fixture artifacts。

## Phase 6：Runtime Revision Loop

交付物：

- 有界 revision pass。
- Verifier error feedback to synthesizer。
- Final status handling。

测试：

- 一次 repair attempt 能修复简单 schema errors。
- 无法修复的 citation errors fail closed。
- Revision count 不能超过 contract。

退出标准：

- Runtime 能从简单 artifact defects 恢复，但不会无限循环。

## Phase 7：真实 LLM Provider

交付物：

- 第一个真实 provider implementation。
- Planning、evidence candidate extraction、synthesis 的 prompt templates。
- 严格 structured-output parsing。
- Malformed response handling。

测试：

- Provider 可 mock。
- Runtime 仍能用 stub provider 通过。
- Malformed provider output 安全失败。

退出标准：

- 真实 provider 能在样例文档上运行，且 verifier 仍是最终权威。

## Phase 8：FastAPI 层

交付物：

- Run creation endpoint。
- Run start endpoint。
- Artifact retrieval endpoint。
- Evidence retrieval endpoint。
- Trace retrieval endpoint。
- Verification report endpoint。

退出标准：

- CLI 和 API 共享同一套 runtime。

## Phase 9：加固

交付物：

- 更多 fixture workspaces。
- 更好的 source classification。
- 更好的 evidence extraction heuristics。
- JSON schema files。
- Database migrations。
- 根据实现情况更新文档。

退出标准：

- MVP 能在真实感项目资料上演示，并能展示可信的 verifier 失败和修正。

## 实现优先级

顺序刻意偏向 verifier：

1. Boundary 和 trace。
2. Evidence store。
3. Stubbed artifact generation。
4. Deterministic verifier。
5. Real LLM。

这样可以避免项目先变成普通总结器，再事后补 citation。Evidence gate 必须先存在，再让流畅生成成为重点。

