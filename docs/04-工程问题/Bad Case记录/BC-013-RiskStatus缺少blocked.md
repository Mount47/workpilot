# BC-013：RiskStatus 缺少 blocked

## 元信息

- 状态：已修复
- 首次发现：2026-07-15
- 主要分类：ENGINEERING_DEFECT
- 次要分类：MODEL_OUTPUT、Domain Modeling
- 关联模块：RiskStatus、RiskDraft、Structured Repair
- 发现 Run：`qwen-plus-bc005-flat-schema-regression`

## 预期结果

来源明确记录项目事项 `blocked` 时，blocker/risk 实体应能表达该状态并由字段 Evidence 验证。

## 实际结果

首轮实体构建和验证后触发业务修订，修订的两次结构化输出最终失败于 `claims.0.risk.status: enum`。现有 RiskStatus 只有 unknown、open、mitigating、closed，不接受来源中的 blocked。

## 根本原因

Risk 模型按传统风险台账设计了生命周期，但同一个实体也承载 ClaimCategory.BLOCKER。领域枚举没有覆盖 blocker 的真实业务状态。

## 修复

- RiskStatus 增加 BLOCKED；
- 仍要求非 unknown 状态提供字段 Evidence；
- EntityFieldVerifier 会检查 `blocked` 是否存在于所引 Evidence；
- 新增 blocked Risk 领域约束测试。

## 回归结果

自动化测试通过。该枚举补充尚未再次使用 qwen-plus 复跑，但输入状态和验证语义均来自已观察来源。

## 面试复盘要点

Schema 枚举不是越窄越安全。过窄会把真实业务状态变成格式错误并浪费重试；正确做法是覆盖明确业务语义，同时要求 Evidence 支持。

