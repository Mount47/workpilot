# 追加项目点设计记录：确定性 Locator 修复与反馈式 Evidence Repair

## 元信息

- 状态：代码与自动化测试完成，真实回归待执行
- 作者：WorkPilot
- 创建日期：2026-07-15
- 最后更新：2026-07-15
- 关联模块：Evidence、Provider、Runtime、Evaluation
- 关联问题：BC-002

## 问题与动机

BC-002 真实运行中，通用 Repair Prompt 额外消耗 862 Token，但 Evidence 接受率没有提升。失败候选中混合了 quote 不存在和 locator 错误，两类问题不应全部交给模型重做。

## 用户场景

模型复制了正确原文，但把 start_line/end_line 算错。系统应使用确定性代码修正 locator，而不是丢弃事实或再次付费调用模型。只有 quote 本身不精确时才需要模型 Repair。

## 产品边界判断

确定性重定位必须以 exact quote 已存在为前提，不允许相似度、编辑距离或语义匹配自动升级为正式 Evidence。

## 目标与非目标

### 目标

- 修复 exact quote 的错误 locator；
- 重复 quote 选择最接近模型建议行号的位置；
- 记录修复次数和原始 locator；
- 给模型提供稳定行号视图；
- Repair 携带脱敏失败原因；
- 保持一次 Repair 上限。

### 非目标

- 不接受模型改写的 quote；
- 不做模糊字符串匹配；
- 不增加第三轮模型调用；
- 不在 Trace 中保存被拒绝正文。

## 最终决策及原因

采用两级修复：代码先修 locator，剩余 exact quote 不匹配再进入模型 Repair。代码能可靠完成的任务不交给模型，可以同时提高接受率并减少无效调用。

## 数据和接口变更

- SourceExtractionReport 增加 `locator_repaired_count` 和 `discard_reason_counts`；
- Evidence metadata 保存 `locator_repaired` 和原始起止行；
- EvidenceQualityReport 聚合安全原因计数；
- EvalCaseResult 增加 `locator_repair_count`；
- Summary 增加 `average_locator_repair_count`。

## 安全、权限和隐私

Trace 和 Repair Prompt 只包含系统错误类型及计数，不包含 discarded quote。行号视图只发送当前已授权来源内容，不扩大数据边界。

## 失败、重试与恢复

- exact quote 存在：确定性修复，不增加模型调用；
- quote 不存在：丢弃并记录原因；
- 某来源全部丢弃：最多一次反馈式 Repair；
- Repair 后仍失败：Run fail closed。

## 可观测性

`evidence_quality_evaluated` 增加 locator 修复数和原因计数；Eval Report 聚合 Locator Repair Count。

## 测试与评测

- 非法行号的 exact quote 被修复；
- 重复 quote 选择最近位置；
- quote 不存在仍丢弃；
- Provider Prompt 包含行号视图；
- Repair Goal 包含原因计数；
- 145 个测试通过。

## 验收标准

- 不降低 exact quote 约束；
- locator 修复不调用额外模型；
- Repair Prompt 不泄露正文；
- BC-001 独立 Suite 可统计修复效果和成本；
- 真实 qwen-plus 回归后更新 BC-002 状态。

## 遗留问题与后续项目点

- qwen-plus 真实回归；
- 根据真实原因分布决定是否增加结构化字段修复；
- 长文档行号视图的 Token 开销评测；
- 多格式 Locator 需要独立策略。
