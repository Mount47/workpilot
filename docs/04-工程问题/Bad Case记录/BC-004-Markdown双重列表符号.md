# BC-004：Markdown 出现双重列表符号

## 元信息

- 状态：已修复
- 首次发现：2026-07-14
- 最后更新：2026-07-15
- 主要分类：ENGINEERING_DEFECT
- 关联模块：Synthesizer、weekly_report.md
- 发现 Run：`qwen-plus-first`、`qwen-plus-bc002-regression`

## 预期结果

Evidence quote 可以保留 Markdown 原文 marker，但周报渲染后每个列表项只能有一个展示 marker。

## 实际结果

Claim text 为 `- PROJ-102...`，Renderer 再增加 `- `，产生：

```text
- - PROJ-102...
```

数字列表原文也会形成 `- 2. ...`，影响报告可读性。

## 根本原因

Evidence 和 explicit Claim 必须保留 exact quote，不能在证据层删除 marker；旧 Renderer 没有区分“验证原文”和“展示文本”。

## 最终修复

- Claim 和 Evidence 原文保持不变；
- Synthesizer 只在展示层移除一个前导 `-`、`*`、`+` 或数字列表 marker；
- Weekly Report 再添加自己的统一 `- `；
- Risk/ActionItem 的 title 使用展示文本；
- Risk description 继续保留 exact Claim 文本。

## 回归测试

- Markdown 不再出现双 `-`；
- 数字 marker 被移除；
- 普通业务文本不受影响；
- Snapshot 中 Claim text 不被修改；
- 结构化 title 和 description 保持不同职责。

## 关联提交

- `8993aff fix(artifacts): normalize source list markers`。

## 面试复盘要点

证据原文和用户展示不能混为一个字段语义。我没有修改 Evidence quote 来美化 Markdown，而是在确定性 Renderer 建立 display projection，既保留验证真实性，也解决展示问题。
