# qwen-plus 首次真实运行问题复盘

## 记录信息

- 日期：2026-07-14
- Run：`qwen-plus-first`
- Provider：阿里云百炼 `qwen`
- Model：`qwen-plus`
- Workspace：仓库内脱敏 `basic_project` Fixture
- 最终状态：passed
- 记录目的：保存真实模型接入后暴露的问题，作为后续优化依据和面试复盘材料

本记录只保存脱敏汇总，不保存 API Key、完整 Prompt 或模型原始响应。

## 运行数据

| 指标 | 实际值 |
|---|---:|
| 扫描文件 | 2 |
| Evidence 模型调用 | 2 |
| 通过验证的 Evidence | 3 |
| 被丢弃 Evidence Candidate | 10 |
| Claim 首次生成调用 | 1 |
| 业务修订调用 | 1 |
| 物理模型调用总数 | 4 |
| 输入 Token | 2507 |
| 输出 Token | 1175 |
| 总 Token | 3682 |
| 模型调用累计延迟 | 20371.786 ms |
| 首次验证 error | 3 |
| 修订次数 | 1 |
| 最终验证检查 | 7 passed |
| Trace 事件 | 91 |

## 正面结果

- 百炼 OpenAI-compatible Adapter 和 API Key 配置可用；
- 两个文本文件均触发 Evidence 抽取调用；
- 无效 quote 和行号没有进入 Evidence Store；
- 第一次 Claim 验证失败后触发受控修订；
- 第二次生成的显式事实与 Evidence 精确匹配；
- Markdown 和 JSON 引用均能回溯到原文；
- Tool success rules、DAG 暂停/恢复、Token 与 Trace 正常工作；
- 最终产物结构完整，没有直接暴露模型原始输出。

## 问题一：Evidence 候选丢弃率过高

### 现象

模型返回的 Candidate 中有 10 条被确定性校验丢弃，只保留 3 条。最终 ProjectSnapshot 的 `source_ids` 只有 `issues.md`，虽然 `meeting_notes.md` 已被扫描并调用模型，但没有 Evidence 进入后续链路。

### 影响

- 周会决策没有进入报告；
- 明确行动项没有进入 `action_items.json`；
- 截止日期和告警增长信息缺失；
- 模型调用已经产生费用，但该来源对最终产物贡献为零。

### 初步原因

Evidence 协议同时要求模型返回 exact quote 和精确行号。真实模型容易出现：

- 去掉 Markdown 序号或列表符号；
- 对原句做轻微改写；
- quote 正确但行号窗口错误；
- 多行内容合并后与源文件换行不一致。

当前 `EvidenceExtractor` 会正确拒绝这些 Candidate，但不会针对高丢弃率执行重新定位或修复。

### 后续措施

- Trace 按 source 记录 candidate_count、accepted_count、discarded_count 和原因分布；
- quote 存在但行号错误时，允许代码确定性重定位；
- quote 轻微改写时禁止自动接受，可要求模型只返回原文片段；
- 单个重要来源 accepted=0 时触发一次定向 Evidence 修复；
- 建立 source_coverage_rate 和 evidence_acceptance_rate 指标。

## 问题二：passed 只证明“已输出内容有效”，不证明“信息完整”

### 现象

最终 3 个 Claim 和 7 个引用检查全部通过，因此 Run 状态为 passed；但周会纪要中的决策、行动项和日期没有进入结果。

### 根因

当前 Verifier 主要检查 precision：

- Claim 引用的 Evidence 是否存在；
- explicit_fact 是否等于 Evidence quote；
- Artifact 中的 Evidence ID 是否能回溯。

它没有检查 recall：

- 是否覆盖所有扫描来源；
- 是否遗漏任务要求中的必要章节；
- 是否遗漏 Golden Dataset 标注的重要事实。

### 设计结论

后续必须区分两个状态：

```text
validity_passed
  已输出事实均可验证

coverage_passed
  任务所需重要信息达到覆盖阈值
```

企业可用的最终 passed 应同时满足两类门槛。

## 问题三：结构化输出重试是同 Prompt 盲重试

### 现象

Provider 在 JSON 或 Pydantic 校验失败后会再次调用模型，但当前没有把压缩后的校验错误反馈给下一次调用。

### 影响

- 第二次可能重复同一格式错误；
- 增加 Token、延迟和费用；
- Trace 尚未明确区分 structured_output_repair 和普通物理调用。

### 后续措施

- 将安全的 Pydantic 错误转换为字段级修复反馈；
- 只要求修复 JSON，不重新执行完整业务推理；
- 增加 `structured_output_repair_requested` Trace；
- 统计 repair_success_rate 和 extra_token_cost。

## 问题四：Evidence 丢弃不会触发重试

JSON 格式合法并不代表 Evidence Candidate 可接受。本次 10 条 Candidate 是在 Provider 成功返回后被业务校验拒绝，因此不会进入结构化输出重试。

这是两个不同阶段：

```text
JSON/Pydantic 失败
  -> Provider 结构修复

Candidate quote/locator 失败
  -> Evidence 定向修复或确定性重定位
```

后续不能用同一个 retry 参数处理两类问题。

## 问题五：业务结构仍有硬编码占位

当前 `risks.json` 中：

- severity 固定为 unknown；
- status 固定为 open；
- mitigation 固定为 null。

`action_items.json` 中：

- owner 固定为 null；
- due_date 固定为 null；
- status 固定为 open。

这避免模型随意控制最终 Schema，但也会丢失原文中已有的负责人和日期。后续应建立正式 Risk、ActionItem、Milestone 领域模型，让模型填充受 Schema 约束的字段，再由代码渲染。

## 问题六：Markdown 出现双重列表符号

Evidence exact quote 本身以 `- ` 开头，Renderer 又增加 `- `，最终形成：

```text
- - PROJ-102: ...
```

修复方向：保留 Evidence 原文不变，但渲染展示文本时安全去除一个前导列表标记。不能修改 Evidence quote，否则会破坏原文验证。

## 问题七：修订历史不足以复盘

最终只持久化最新 `project_snapshot.json` 和最终验证报告。Trace 能看到 attempt 和 error_count，但没有保存：

- 每轮 Snapshot 的安全差异；
- 失败 check_id 分布；
- Claim 新增、删除和引用变化；
- 修订前后验证结果。

后续应保存 attempt 级 Manifest 和结构化 Diff，但仍避免保存不必要的敏感正文。

## 问题八：真实成本还不能直接计算

Trace 已记录 Provider、Model 和 Token，但 `estimated_cost` 为 null。需要版本化价格目录，并明确地域、模型版本和生效时间，才能生成可审计成本。

价格配置变化频繁，不应把单价硬编码在业务逻辑中。

## 优先级

| 优先级 | 项目 | 原因 |
|---|---|---|
| P0 | Source Coverage 与 Evidence Acceptance 指标 | 当前 passed 可能漏掉整个来源 |
| P0 | Evidence 定向修复和安全重定位 | 直接影响报告完整度与调用浪费 |
| P1 | 结构化输出带错误反馈修复 | 降低重复错误和额外费用 |
| P1 | Risk/ActionItem 正式领域模型 | 企业业务输出需要结构化负责人和日期 |
| P1 | Attempt Diff 与失败 check 持久化 | 支撑调试、评测和面试解释 |
| P2 | Markdown 列表符号规范化 | 展示质量问题，修复成本低 |
| P2 | 版本化模型价格目录 | 用于成本评测，不影响正确性主链路 |

## 面试复盘表述

可以这样描述本次真实运行：

> 首次接入 qwen-plus 后，系统最终引用准确率通过，但我通过 Trace 发现 13 条 Evidence Candidate 中有 10 条因 exact quote 或 locator 校验被拒绝，导致一个已扫描来源完全未进入报告。这说明引用 Precision 通过不等于信息 Recall 达标。因此我没有把 passed 当成项目完成，而是将 Source Coverage、Evidence Acceptance、定向证据修复和双门控状态列为下一阶段核心指标。

该案例比“成功调用模型并生成报告”更能体现工程判断、评测意识和企业级可靠性设计。
