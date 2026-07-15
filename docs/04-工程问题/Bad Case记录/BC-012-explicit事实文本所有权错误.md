# BC-012：explicit 事实文本所有权错误

## 元信息

- 状态：已回归验证
- 首次发现：2026-07-15
- 主要分类：VERIFICATION_GAP
- 次要分类：MODEL_OUTPUT、Domain Modeling
- 关联模块：ClaimDraft、ClaimBuilder、ClaimSupportVerifier
- 发现 Run：`qwen-plus-bc005-flat-schema-regression`

## 预期结果

explicit_fact 的正文应始终来自已验证 Evidence quote，模型只负责选择哪条 Evidence 是该 Claim 的主事实。

## 实际结果

扁平 Schema 回归成功构建 15 个 Claim 和结构化实体，但 15 个 explicit Claim 全部未通过 exact quote 校验，Claim Support 为 0%。`claim_text_repair_count` 也是 0，说明模型输出并非只有列表 marker 差异。

字段级 Evidence Support 为 100%，因此问题不在 owner/date 字段，而在 Claim text 仍由模型重新生成。

## 根本原因

旧契约同时让模型选择 Evidence 和重新抄写事实正文。即使 Prompt 要求 exact quote，模型仍可能改标点、格式或措辞；确定性修复只能覆盖单个 marker，无法安全接受一般改写。

## 修复

- ClaimDraft 增加可选 `primary_evidence_ref`；
- explicit_fact 可以明确选择父 Evidence 中的一条主来源；
- 只有一个 Evidence ref 时自动将其作为 primary；
- ClaimBuilder 直接使用 primary Evidence quote 作为 Claim text；
- 多 Evidence 且没有 primary 时，仍只接受唯一 marker-normalized 精确匹配；
- primary 不属于父 refs 时在 Draft 阶段拒绝；
- derived_fact、analytical_judgement 禁止选择 primary Evidence。

## 回归结果

单来源、显式 primary、非法 primary 和多来源 paraphrase 测试通过。该问题由确定性代码契约修复，不需要模糊匹配。

修复后 qwen-plus 回归 Claim Support、Citation Validity 和 Claim Source Coverage 均为 100%，Run 最终 passed。

## 面试复盘要点

对 explicit fact，LLM 应输出“证据身份”，而不是再次生成事实内容。把事实文本所有权交回 Evidence Store，可以从架构上消除一类幻觉和格式漂移。
