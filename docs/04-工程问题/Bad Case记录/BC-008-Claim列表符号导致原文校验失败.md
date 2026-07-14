# BC-008：Claim 列表符号导致原文校验失败

## 元信息

- 状态：已修复
- 首次发现：2026-07-15
- 主要分类：VERIFICATION_GAP
- 次要分类：MODEL_OUTPUT、ENGINEERING_DEFECT
- 关联模块：ClaimBuilder、ClaimSupportVerifier
- 发现 Run：`qwen-plus-bc005-regression-repair`

## 预期结果

explicit_fact 必须等于所引 Evidence 原文。模型仅漏掉 Markdown 列表 marker 时，系统可以做无语义变化的确定性还原；真正的改写仍必须失败。

## 实际结果

修复后真实回归首次 Claim 构建成功，但 15 个 explicit Claim 全部触发 `claim.explicit_quote_match`。模型保留正文却去掉了 Evidence 开头的 `- ` 或数字列表 marker，导致业务修订被触发。

## 根本原因

Evidence 抽取契约要求保留 Markdown marker，Claim 模型仍会习惯性输出干净正文。Artifact 已处理双 marker，但 ClaimBuilder 尚未在正式 Claim 进入验证前做同等级别的确定性规范化。

## 修复

- 只对 explicit_fact 生效；
- Claim 与所引 Evidence 各移除最多一个前导列表 marker 后比较；
- 只有唯一 Evidence 完全匹配时，才使用 Evidence 原文替换 Claim text；
- ActionItem/Risk description 同步使用规范化后的 Claim text；
- Trace 的 `claims_built` 记录 `claim_text_repair_count`；
- 任何其他文字差异仍视为 paraphrase，不自动修复。

## 回归结果

marker-only 修复与 paraphrase 拒绝自动化测试通过，因此标记为“已修复”。尚未为该修复再次调用真实模型。

## 面试复盘要点

这不是放宽事实校验，而是将来源格式恢复为唯一标准表示。修复条件必须比业务语义更严格，才能避免“相似文本”被错误升级为可引用事实。

