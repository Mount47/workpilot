# 项目总览

WorkPilot 是一个面向项目知识工作的"有边界但自主" Agent。它接收明确任务和受限 workspace，产出可验证的交付物：带引用的周报、结构化风险、结构化行动项、验证报告和执行追踪。

核心原则：

> 自由的是路径，受控的是边界。

Agent 可以自主决定如何检查文件、规划步骤、检索证据、组织输出；但不能越过 workspace、不能编造证据、不能硬填未知负责人或日期、不能生成无来源结论，也不能无限循环消耗预算。

## 主架构

```text
Mission Contract
  -> Plan
  -> Retrieve
  -> Synthesize with Citations
  -> Verify
  -> Artifact + Task Drafts
  -> Trace
```

## 文档结构

### 产品设计（Product Design）

| 文档 | 说明 |
|---|---|
| [定位与目标](./design/product_scope.md) | 项目定位、目标、非目标和 MVP 边界 |
| [核心架构](./design/architecture.md) | 系统组件、运行时边界、组件交互 |
| [数据模型](./design/data_model.md) | 实体定义、字段规范、校验规则 |

### 接口设计（Interface Design）

| 文档 | 说明 |
|---|---|
| [接口规划](./interface/planning.md) | CLI 和 API 设计（规划接口） |
| [接口实现](./interface/implementation.md) | CLI 和 API 实现进度（实际交付） |

### 开发规范（Development）

| 文档 | 说明 |
|---|---|
| [Agent Runtime](./dev/agent_runtime.md) | 受控工作流、步骤生命周期、状态机 |
| [Verifier 规则](./dev/verifier_rules.md) | 引用、支撑、结构、trace 检查规则 |
| [Provider 架构](./dev/provider_architecture.md) | 多模型 API 支持架构（GPT/Claude/GLM/DeepSeek/Qwen/百炼） |
| [开发方案](./dev/implementation_plan.md) | 五阶段实现方案（阶段0-5） |

### 归档（Archive）

历史文档已迁移到 [archive/readme.md](./archive/readme.md) 查看清单。

---

## 核心设计哲学

1. **Evidence Gate**：每条结论必须可追溯到 Evidence Store 中的 evidence
2. **Verifier 是护城河**：确定性检查优先于 LLM 自评
3. **受控 Runtime**：状态机驱动，LLM 只在被调用时参与，永不掌控控制流
4. **Fail Closed**：证据缺失时拒绝输出，宁可不完整也不编造
