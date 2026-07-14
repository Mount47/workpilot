# Bad Case 记录规范

## 判定标准

满足以下任意条件即可登记：

- Runtime 异常失败或错误终止；
- Run passed，但任务要求没有完整完成；
- 结论、引用、结构化字段或 Artifact 明显错误；
- Agent 采取了无效或重复动作；
- Provider、预算、Trace、持久化或安全行为偏离设计；
- 自动化评测出现指标回退；
- 已修复问题再次出现。

## 分类

```text
MODEL_OUTPUT
EVIDENCE_RETRIEVAL
VERIFICATION_GAP
AGENT_LOOP
PROVIDER_FAILURE
OBSERVABILITY
SECURITY
PERFORMANCE_COST
EVALUATION_REGRESSION
ENGINEERING_DEFECT
```

一个案例可以拥有多个分类，但应明确一个主要分类。

## 编号与文件名

```text
BC-001-简短中文标题.md
BC-002-简短中文标题.md
```

ID 永不复用。案例被证明无效时保留记录，并将状态改为接受风险或补充结论。

## 必填证据

至少包含：

1. 预期结果；
2. 实际结果；
3. 可复现输入概况；
4. Trace Event、Artifact 或测试失败依据；
5. 影响范围；
6. 直接原因与根因；
7. 修复和回归状态。

## 修复闭环

```text
发现
  -> 登记 Bad Case
  -> 最小复现
  -> 根因定位
  -> 修复代码
  -> 新增回归测试
  -> 更新评测指标/样本
  -> 复跑原始场景
  -> 更新状态与复盘结论
```

## 安全要求

- API Key 只记录对应环境变量名称，不记录值；
- 企业文档只记录脱敏摘要、Hash 或定位信息；
- Prompt 只保留必要的结构和版本，不复制敏感正文；
- Trace 引用优先记录 event_type、sequence、step_id 和统计；
- 运行目录可能不纳入 Git，文档必须保存足够的非敏感摘要。

## 面试复盘要求

每个高价值案例最后回答：

- 为什么原设计没有发现它；
- 你如何用 Trace 定位；
- 为什么选择当前修复，而不是堆模型或无限重试；
- 如何通过自动化测试和真实回归证明修复；
- 修复增加了多少延迟、Token 和复杂度；
- 还存在哪些没有解决的边界。
