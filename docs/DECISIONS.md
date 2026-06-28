# 架构决策记录（ADR）

> 追加式文档。只记录"为什么这样做"，不记录"做了什么"（那是代码的事）。
> 新决策追加到末尾，已有决策不删除，只能标记 superseded。

---

## ADR-001: 证据门控而非 LLM 自评

日期: 2026-06-28
状态: accepted

**决策**: 所有生成结论必须引用 Evidence Store 中的 evidence ID，缺少证据时输出 unknown 而非编造。

**为什么**: LLM 自评（让模型判断自己输出是否正确）在实践中不可靠——模型倾向于为自己的输出辩护。证据门控将"是否可信"的判断从模型转移到确定性代码，形成硬约束。

**否决了什么**: (1) LLM-as-judge 自评层——无法保证一致性；(2) 纯人工审核——不可扩展；(3) 无验证直接输出——违背项目核心价值。

**影响**: Evidence Store 成为 retrieval 和 generation 之间的可信边界。Synthesizer 只能引用已注册的 evidence。Verifier 检查引用有效性。

---

## ADR-002: 状态机驱动而非自由循环

日期: 2026-06-28
状态: accepted

**决策**: Runtime 使用显式状态机（pending → planning → retrieving → synthesizing → verifying → passed/failed）驱动执行流，LLM 只在被调用时参与。

**为什么**: AutoGPT 式自由循环不可预测——无法保证终止、无法控制成本、难以 debug。状态机让每一步可观测、可中断、可复现。

**否决了什么**: (1) ReAct 自由循环——LLM 决定何时停止，成本不可控；(2) 多 Agent 框架——复杂度过高，MVP 不需要；(3) 纯 DAG 编排——灵活性不足，不支持 verify-revise 回环。

**影响**: Runtime 拥有控制流。状态转换有明确校验（terminal state 不可逆）。每步执行前做 contract 检查。Trace 记录完整状态变迁。

---

## ADR-003: Verifier 优先于 LLM 生成

日期: 2026-06-28
状态: accepted

**决策**: 开发优先级上，确定性 Verifier 先于真实 LLM 生成能力实现（Phase 3 在 Phase 4 之前）。

**为什么**: 如果先做生成再做验证，中间阶段的输出不可信且无法度量质量。先建验证能力，再接入 LLM，可以立即量化生成质量、快速定位幻觉。"先有尺子再有产品"。

**否决了什么**: (1) 先接 LLM 再补验证——中间产出不可度量；(2) 同时开发——精力分散，且验证是生成的前置依赖。

**影响**: Phase 3（Verifier）在 Phase 4（Real LLM）之前。Stub artifacts 必须足够结构化以被 Verifier 检查。实现优先级：Boundary > Evidence Store > Stub generation > Verifier > Real LLM。

---

## ADR-004: Stub Provider 保证离线可测

日期: 2026-06-28
状态: accepted

**决策**: 始终保留 StubProvider，返回确定性 fixture 数据，确保全部测试和演示可在无网络环境运行。

**为什么**: Agent 项目的测试最大痛点是外部依赖（API key、网络、模型版本变化）。Stub Provider 让 CI 稳定、本地开发零配置、演示不依赖网络状态。

**否决了什么**: (1) 只用 mock（unittest.mock）——mock 不验证接口契约；(2) 录制/回放（VCR）——维护成本高且响应格式变化时脆弱。

**影响**: Provider 抽象必须足够薄，使 Stub 能覆盖全部接口。`--provider stub` 是 CLI 默认值。smoke test 零网络依赖。

---

## ADR-005: 宁可不完整也不编造（Fail Closed）

日期: 2026-06-28
状态: accepted

**决策**: 当证据不足以支撑某个字段时，输出显式 unknown / null，而非用 LLM 生成看似合理的填充。

**为什么**: 项目知识工作的消费者（PM、管理层）往往直接信任 Agent 输出。一个"看起来对"的编造比一个明确的 unknown 危害大得多——前者导致错误决策，后者只是信息缺失。

**否决了什么**: (1) 用 LLM 推测填充 + 低置信度标记——用户实际使用中会忽略置信度标记；(2) 完全跳过缺失字段——结构不完整会破坏下游消费。

**影响**: Synthesizer 在缺少 owner/date 时写入 `"unknown"`。Schema 中这些字段允许 null。Verifier 不因 unknown 判定 fail（unknown 是合法状态）。

---

## ADR-006: 文档三层分级 + 真值优先级

日期: 2026-06-28
状态: accepted

**决策**: 文档分为三层——动态层（STATUS.md，一屏）、追加式 ADR（DECISIONS.md，只加不删）、稳定层（design/ 下的架构文档）。声明真值优先级：代码+测试 > git log > 文档。

**为什么**: Agent 项目迭代快，传统"一份大文档"必然过时。分层让每层有明确更新频率和维护责任。真值优先级声明让读者知道文档可能滞后，降低信任误差。

**否决了什么**: (1) 单一 README 承载所有信息——信息过载且更新频率冲突；(2) Wiki——个人项目维护成本不值得；(3) 不写文档——丧失面试叙事能力和协作者入口。

**影响**: STATUS.md 随每次有意义的变更更新。DECISIONS.md 每次架构取舍时追加。design/ 只在大方向调整时修改。docs/README.md 作为外部读者入口。
