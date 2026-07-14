# BC-011：嵌套字段 Schema 阻塞 qwen 输出

## 元信息

- 状态：修复中
- 首次发现：2026-07-15
- 主要分类：MODEL_OUTPUT
- 次要分类：ENGINEERING_DEFECT、PERFORMANCE_COST
- 关联模块：ClaimBuilder、ActionItemDraft、RiskDraft
- 发现 Run：`qwen-plus-bc005-golden-regression`

## 预期结果

模型应以稳定、低复杂度的 JSON 返回负责人、日期和缓解措施；系统再将结果转换为带字段 Evidence 的严格领域对象。

## 实际结果

第三次 BC-005 qwen-plus 回归保持 15/15 Evidence 接受，但首次 Claim 构建连续两次失败。最终脱敏错误集中在 `risk.owner`、`risk.mitigation` 和 `action_item.due_date_text` 的 `model_type`。

模型将这些业务值自然地返回为字符串或 null，而 Draft Schema 要求 `{value, evidence_refs}` 嵌套对象。失败 Run 使用 9443 Token、耗时 62.61 秒，未进入 Artifact 和 Golden 字段评测。

## 根本原因

系统直接复用了严格领域对象 SupportedText 作为模型输出契约。该结构适合内部持久化和验证，但要求模型为每个简单字段重复生成嵌套对象，增加了 Schema 深度和出错面。

## 修复

模型侧 Draft 改为扁平结构：

```text
owner: string | null
owner_evidence_refs: string[]
due_date_text: string | null
due_date_evidence_refs: string[]
mitigation: string | null
mitigation_evidence_refs: string[]
```

Draft Validator 仍强制非空值必须有 refs、空值不能引用 Evidence。ClaimBuilder 在响应通过后，将扁平字段组装为严格 SupportedText，因此领域模型、字段 Verifier 和 Artifact Schema 不需要放宽。

## 回归状态

- 扁平字段配对约束测试通过；
- JSON Schema 测试确认模型侧字段不含 SupportedText `$ref`；
- 领域对象物化与字段 Evidence 测试通过；
- 全量 171 项测试通过；
- 尚未再次调用 qwen-plus，因此状态为“修复中”。

## 面试复盘要点

领域模型和 LLM 传输 Schema 不必完全相同。内部模型应追求强约束，模型输出契约应追求低歧义；通过显式 Mapper 可以同时保留可靠性和模型兼容性。

