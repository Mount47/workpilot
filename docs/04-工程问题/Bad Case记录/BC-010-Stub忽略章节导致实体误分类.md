# BC-010：Stub 忽略章节导致实体误分类

## 元信息

- 状态：已修复
- 首次发现：2026-07-15
- 主要分类：EVALUATION_REGRESSION
- 次要分类：ENGINEERING_DEFECT
- 关联模块：StubProvider、Golden Evaluation
- 发现方式：BC-005 Golden Stub 基线

## 预期结果

确定性 Stub 应为离线评测提供稳定、可解释的 Evidence 分类。“下一步”章节中的责任人行动必须优先分类为 action_item。

## 实际结果

BC-005 第一版 Golden Stub 运行中，3 个行动项只匹配 2 个，Action Entity Recall 为 66.67%。`- 张三：协调 SRE 排查告警上升原因。` 因包含“告警”和“上升”，在 action 关键词之前被分类为 risk。

## 根本原因

StubProvider 逐行分类时直接跳过 Markdown 标题，没有保存当前章节。分类只能依靠单行关键词，而风险关键词优先级高于行动关键词。

## 修复

- 扫描 Markdown 时保留最近章节标题；
- “下一步”“行动项”“待办”章节优先分类为 action_item；
- 其他章节继续使用原关键词规则；
- 新增同一“告警”语义在讨论章节为 risk、在下一步章节为 action_item 的测试。

## 回归结果

BC-005 Stub 基线 Action/Risk Entity Recall 均为 100%，全量 169 项测试通过。

## 面试复盘要点

评测工具本身也会产生 Bad Case。如果确定性基线分类错误，模型对比数据就失去可信度；因此 Golden 数据、匹配器和 Stub 都需要和生产链路一样接受回归测试。

