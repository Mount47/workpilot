# 最终目标

## 一句话定位

WorkPilot 是一个证据门控的项目周报 Agent——从散落的项目资料中抽取事实，生成可追溯的结构化报告，并用确定性验证拦截幻觉。

## 解决的痛点

| 痛点 | 现状 | WorkPilot 的解法 |
|------|------|------------------|
| 周报耗时 | PM/TL 每周花 2-3 小时整合散落信息 | 自动扫描 workspace，抽取证据，生成报告 |
| LLM 输出不可信 | 直接用 ChatGPT 总结会编造人名和日期 | Evidence Gate：每条结论带引用，缺失标 unknown |
| 无法追溯 | 报告里的结论不知道从哪来 | 全部结论关联 source_file:line_range |
| 质量不可度量 | 不知道生成结果好不好 | 确定性 Verifier + 自动化评测，量化 pass/fail |

## 面试叙事的三个支点

### 1. 工程决策力（不是"用了什么框架"）

能回答："你做了哪些关键取舍？"
- 为什么用证据门控而非 LLM 自评？（ADR-001）
- 为什么状态机而非 ReAct 循环？（ADR-002）
- 为什么 Verifier 优先于生成？（ADR-003）
- 为什么 Fail Closed？（ADR-005）

### 2. 端到端可运行（不是 PPT 架构）

能演示：`workpilot run` 从真实项目资料生成周报，Verifier 拦截一条幻觉引用，trace 解释为什么。

### 3. 可量化的效果（不是"做了一个 Agent"）

能报数：
- Citation accuracy: X%（引用指向真实来源的比例）
- Hallucination rejection rate: Y%（Verifier 拦截率）
- Evidence coverage: Z%（报告中有引用支撑的结论占比）
- Latency: 从输入到输出的端到端时间

## 产品级完成标准

以下是"可以拿去面试"的最低完成线：

### 必须完成

1. **真实 LLM 跑通端到端**
   - 接入至少一个真实 Provider（OpenAI-compatible，覆盖 DeepSeek/Qwen/GLM）
   - 从真实项目资料生成有意义的周报（不是 stub 占位符）
   - Structured output 解析，malformed response 安全失败

2. **Citation Verifier 上线**
   - 检查每个引用的 evidence_id 是否存在
   - 检查 quote 是否匹配源文件实际内容
   - 能 reject 一个包含幻觉引用的 artifact 并输出具体错误位置

3. **自动化评测**
   - 准备 3-5 个真实 workspace fixture（不同复杂度）
   - 每个 fixture 有 golden answer（人工标注的预期引用）
   - 一条命令跑完评测，输出 precision/recall/F1
   - 评测结果可复现（固定 seed + temperature=0）

4. **可观测性**
   - Trace 记录每步耗时、token 消耗、LLM 调用详情
   - 运行结束输出成本摘要（total tokens, estimated cost）
   - 失败时 trace 能定位到具体哪步出错

5. **工程质量**
   - 测试覆盖率 > 80%（核心路径 100%）
   - CI 通过（pytest + type check）
   - 无 API key 时全部测试仍能通过（stub fallback）

### 加分项（有时间就做）

6. **Support Verifier（LLM-Judge）**
   - 判断"结论是否被证据充分支撑"（不只是引用存在）
   - 与确定性 Verifier 分层，LLM-Judge 是辅助不是权威

7. **异步并发优化**
   - 多文件 evidence 抽取并行
   - Provider 调用异步化
   - 大 workspace 不超时

8. **多 Provider 切换**
   - Claude + OpenAI-compatible
   - 运行时切换，对比不同模型输出质量

## 非目标（明确不做）

- Web UI（CLI + JSON 输出足够支撑面试演示）
- 第三方集成（Jira/Slack/GitHub 拉取）
- 多用户/权限/持久化存储
- 部署和运维（本地运行即可）
- PDF/DOCX 解析（纯文本 + Markdown + JSON + CSV）

## 技术栈最终形态

```
Python 3.11+ / Pydantic v2 / Typer CLI
LLM: OpenAI-compatible API (structured output)
测试: pytest + pytest-cov
类型检查: mypy (strict)
评测: 自建 eval harness (fixture + golden answer)
CI: GitHub Actions (test + type check + lint)
```

## 时间预估

假设每天 2-3 小时有效开发时间：

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 2 | Evidence 抽取逻辑 + 测试 | 2-3 天 |
| Phase 3 | Citation Verifier + 测试 | 2-3 天 |
| Phase 4 | Real LLM Provider + 端到端 | 3-4 天 |
| Eval | 评测 harness + fixtures | 2-3 天 |
| Polish | 可观测性 + CI + 文档 | 2-3 天 |
| **总计** | | **~2-3 周** |
