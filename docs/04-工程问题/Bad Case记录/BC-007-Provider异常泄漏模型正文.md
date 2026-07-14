# BC-007：Provider 异常泄漏模型正文

## 元信息

- 状态：已修复
- 首次发现：2026-07-15
- 主要分类：SECURITY
- 次要分类：OBSERVABILITY、ENGINEERING_DEFECT
- 关联模块：OpenAIProvider、ClaudeProvider、Trace、Eval
- 发现 Run：`qwen-plus-bc005-regression`

## 预期结果

Trace、RunContext 和 Eval 可以记录稳定错误类型与安全摘要，但不能记录模型响应正文或企业业务内容。

## 实际结果

结构化重试耗尽后，ProviderResponseError 拼接 `Raw: {text[:200]}`。Runtime 将该异常写入 step_failed、run_failed、run_context 和 eval_report，导致 Claim 原文片段进入多个观测产物。

## 根本原因

Provider 为方便调试直接将原始响应摘要作为异常消息，而上层默认认为异常消息可以安全持久化。安全边界在 Provider 和 Trace 之间不一致。

## 修复

- ProviderResponseError 不再包含 Raw response；
- 最终错误只包含尝试次数、字段路径和错误类型；
- Pydantic 错误提取时使用 `include_input=False`；
- 自动化测试使用敏感占位文本，断言最终异常中不存在字段值和原始键名。

## 回归结果

OpenAI-compatible 与 Claude Mock 回归测试通过。该泄漏由确定性代码路径造成，测试已覆盖最终异常，因此标记为“已修复”；历史运行文件仍属于本地敏感产物，不提交 Git。

## 面试复盘要点

“不主动记录 Prompt”不足以保证日志安全。异常字符串、SDK 错误和校验 input 都可能成为旁路，必须把安全错误摘要定义为 Provider 边界的一部分。

