# WorkPilot 文档中心

> **真值优先级**：代码+测试 > git log > 文档。
> 本文档体系是导航和决策记录，不是权威来源。当文档与代码冲突时，以代码为准。

## 系统概览

WorkPilot 是一个证据门控的项目知识工作 Agent。给定一组项目资料（会议纪要、issue、PR 摘要等），它自动生成带引用的周报、结构化风险清单和行动项。

与普通 LLM 总结器的区别：**每条结论必须追溯到源文件的具体行**。缺少证据时输出 unknown，不编造。

核心执行流程：

```
Mission Contract → Plan → Retrieve → Extract Evidence → Synthesize → Verify → Artifacts
```

关键约束：
- 文件访问沙箱化，不可越过 workspace 边界
- 控制流由状态机驱动，LLM 只在被调用时参与
- 确定性 Verifier 检查引用有效性，拒绝无来源结论
- 预算（步数/token/时间）耗尽时强制停止

## 文档分层

| 层级 | 文件 | 更新频率 | 说明 |
|------|------|----------|------|
| 动态层 | [STATUS.md](./STATUS.md) | 每次有意义的变更 | 一屏：进度、最近变更、已知阻塞 |
| 决策层 | [DECISIONS.md](./DECISIONS.md) | 每次架构取舍 | 追加式 ADR：决策/为什么/否决了什么 |
| 稳定层 | design/ | 大方向调整时 | 架构、数据模型、产品范围 |

## 稳定层文档

| 文档 | 说明 |
|------|------|
| [产品范围](./design/product_scope.md) | 定位、目标、非目标、MVP 边界 |
| [核心架构](./design/architecture.md) | 系统组件、运行时边界、组件交互 |
| [数据模型](./design/data_model.md) | 实体定义、字段规范、校验规则 |

## 开发规范

| 文档 | 说明 |
|------|------|
| [Agent Runtime](./dev/agent_runtime.md) | 受控工作流、状态机、步骤生命周期 |
| [Case 处理过程可视化](./pipeline-viz.html) | 可点击执行的 AI case 输入/输出/trace 演示 |
| [Verifier 规则](./dev/verifier_rules.md) | 引用、支撑、结构、trace 检查规则 |
| [Provider 架构](./dev/provider_architecture.md) | 多模型 API 支持（GPT/Claude/GLM/DeepSeek/Qwen） |
| [开发方案](./dev/implementation_plan.md) | 五阶段递进实现计划 |

## 接口设计

| 文档 | 说明 |
|------|------|
| [接口规划](./interface/planning.md) | CLI 和 API 设计规划 |
| [接口实现](./interface/implementation.md) | 实际交付进度 |

## 快速体验

```bash
# 安装
git clone <repo-url> && cd WorkPilot
pip install -e ".[dev]"

# 运行 stub 演示（无需 API key）
workpilot run \
  --workspace ./tests/fixtures/workspaces/basic_project \
  --goal "生成本周项目周报" \
  --provider stub \
  --output ./runs/demo

# 查看执行追踪
workpilot trace --run ./runs/demo

# 运行测试
pytest
```

## 归档

历史文档见 [archive/readme.md](./archive/readme.md)。
