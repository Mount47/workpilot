# 整体架构

## 高层系统

```text
CLI / API
  -> Mission Contract Service
  -> Agent Runtime
      -> Planner
      -> Workspace Tools
      -> Evidence Store
      -> Synthesizer
      -> Verifier
      -> Trace Journal
  -> Artifact Store
```

Runtime 是一个单 Agent、受控工作流。Planner、Synthesizer、Verifier 可以使用不同 prompt 或 provider 调用，但它们不是拥有自由控制流的独立 Agent。

## 核心组件

### Mission Contract Service

创建并校验单次运行边界：

- 用户目标
- workspace root
- 允许工具
- 最大步骤数
- token 预算
- 时间预算
- 允许输出的 artifact 类型
- 禁止动作
- 输出目录

每个计划步骤和工具调用执行前，都必须对照 contract 校验。

### Agent Runtime

Runtime 拥有 run 的状态机，执行主链路：

```text
create_contract
  -> plan
  -> retrieve
  -> synthesize
  -> verify
  -> revise_if_allowed
  -> finalize
```

Runtime 决定 verifier 失败后是否允许一次受限修正。达到步骤、时间或 token 预算时必须停止。

### Planner

把 mission 拆成有边界的计划。Planner 可以提出步骤，但 Runtime 负责校验和执行。Planner 输出应是结构化数据，而不是纯自然语言。

示例 step types：

- `scan_workspace`
- `classify_sources`
- `search_documents`
- `extract_evidence`
- `synthesize_artifacts`
- `verify_artifacts`
- `revise_artifacts`
- `finalize_run`

### Workspace Tools

用于读取项目资料的受控工具：

- 列出 workspace 内文件。
- 按相对路径读取文件。
- 文本搜索。
- 按 line range 抽取片段。
- 注册 evidence snippet。

所有路径都必须 normalize，并确认仍在 workspace root 下。

### Evidence Store

Evidence Store 是 retrieval 和 generation 之间的可信边界。Artifact 只能引用 evidence store 中存在的 evidence id。

它需要支持：

- 插入 evidence snippets。
- 去重等价 snippets。
- 按 ID 获取 evidence。
- 按 source、type、extraction step 列出 evidence。
- 用当前文件内容验证 source text。

### Synthesizer

基于以下输入生成 artifacts：

- Mission contract
- Plan
- 相关 source metadata
- Evidence store entries
- Artifact schema

Synthesizer 必须给关键结论生成 source refs，并在缺少负责人或日期时输出明确 unknown。

### Verifier

Verifier 不是 LLM 自评层，而是一组面向文件、evidence record、artifact JSON、report citations 的具体检查。后续可以增加 LLM 辅助判断，但确定性检查是 MVP 必须项。

Verifier 层次：

- Citation verifier
- Support verifier
- Task schema verifier
- Trace verifier
- Contract verifier

### Trace Journal

Trace Journal 记录可复盘的 run 过程：

- 计划步骤
- 工具调用
- 输入摘要
- 输出摘要
- 创建的 evidence
- verifier 失败
- 修正尝试
- 最终状态

Trace 是产品能力，不只是 debug log。

## 存储

MVP 使用 SQLite 足够。建议用 SQLAlchemy，方便后续切换 PostgreSQL。

建议持久化对象：

- `runs`
- `mission_contracts`
- `sources`
- `evidence`
- `plans`
- `artifacts`
- `verification_reports`
- `trace_events`

Artifacts 可以写入文件系统，并在数据库中索引。

## LLM Provider 边界

LLM provider 必须抽象在接口后面：

- `generate_plan`
- `extract_evidence_candidates`
- `synthesize_report`
- `synthesize_risks`
- `synthesize_action_items`
- `revise_artifacts`

MVP 必须包含 stub provider，用于测试和演示。Runtime 应该能在无网络环境下测试。

