# 产品范围

## 定位

WorkPilot 是面向项目资料的真实任务 Agent。它不是文档问答工具，不是普通总结器，也不是办公自动化大杂烩。它接收一个有边界的 mission，在允许的 workspace 内工作，收集证据，生成交付物，并验证每个关键结论是否可追溯。

第一条有用主场景是：

> 根据项目资料，生成本周项目周报、风险清单和下周行动项。

## MVP 输入

MVP 假设用户把项目资料上传或放入 workspace：

- 会议纪要
- issue 列表
- PR 或 commit 摘要
- 需求变更记录
- 项目说明文档

初期文件类型可以先支持纯文本、Markdown、JSON、CSV。PDF、DOCX、远程系统和二进制解析都放到后续阶段。

## MVP 输出

Runtime 生成一个 run 目录，包含：

- `weekly_report.md`：带引用的项目周报。
- `risks.json`：结构化风险清单。
- `action_items.json`：结构化下周行动项草稿。
- `verification_report.json`：基于规则和确定性检查的 verifier 报告。
- `trace.json`：Agent 执行过程日志。

## 目标

- 每次运行都创建并持久化 Mission Contract。
- Agent 每一步动作都必须经过 contract 检查。
- 文件访问限制在配置的 workspace 内。
- 所有被引用证据都进入 Evidence Store。
- 关键结论必须带 source refs。
- 验证引用是否指向真实来源文本。
- 验证不同类型结论是否由合适证据支撑。
- 缺少负责人、日期等信息时标记 unknown，而不是脑补。
- 控制流由应用代码掌握，不交给 LLM 自由循环。
- 支持 stub LLM provider，便于测试和离线演示。

## 非目标

- MVP 不做邮件、日历、Slack、Jira、GitHub、Linear、Notion 集成。
- 不做自动外发通知或写回第三方系统。
- 不处理高危事务动作。
- 不把复杂多 Agent 框架作为核心控制模型。
- 不做 AutoGPT 式开放循环。
- 不允许无限制文件系统、网络或 shell 访问。
- 不允许绕过 evidence verifier 生成结论。
- 在 Agent Runtime 和 Verifier 稳定前，不优先做精致 Web UI。
- 不把产品定位扩展成泛办公助手。

## 产品原则

WorkPilot 在“如何完成知识工作”上应该有自主性，但在“允许断言什么”上必须保守。系统应该宁可输出不完整但诚实的结果，也不要输出流畅但无证据的结果。

## MVP 完成标准

一次 MVP run 成功的标准：

- 创建并保存 mission contract。
- 通过受控工具扫描 workspace。
- 识别相关来源文件。
- 抽取带 source id 和 line range 的 evidence snippet。
- 只用 evidence refs 或显式 unknown 生成 artifacts。
- verifier 能输出 pass/fail 和可执行的错误位置。
- trace 能解释 Agent 为什么生成这些报告。

