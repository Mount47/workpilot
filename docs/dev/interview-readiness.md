# 面试就绪度评估与路径

> 日期: 2026-07-03
> 真值优先级: 代码+测试 > git log > 文档。本文件是一次诚实的现状评估和行动路径，不是权威状态源。
> 定位: 把项目从"漂亮的骨架"推到"精而实、能扛追问"的作品。

## 一句话结论

当前项目在**设计判断力**维度已经能打，但在**可运行 + 有实测数字**维度是空的。
状态是"精在架构叙事，虚在结果"——能撑住面试前 15 分钟的设计讨论，撑不住第 16 分钟的"给我看真实结果和数字"。

---

## 一、已经 solid 的部分（护城河，不用改）

1. **candidate vs evidence 的信任分级 + 精确子串校验取代向量 RAG**
   - 这是大多数候选人拿不出来的差异化点。
   - 回答的不是"怎么检索相关内容"（RAG 的问题），而是"怎么证明这条结论在源文件里真实存在"——一个更窄但更硬的问题定义。
   - `EvidenceExtractor._validate_and_assign_id()` 四道校验：quote 非空 → quote 是源文件精确子串 → 行号范围合法 → quote 落在声明行号窗口内。只有全过的 candidate 才升级为 evidence 并分配稳定 ID。

2. **一套互相咬合的 ADR 取舍逻辑**
   - ADR-001 证据门控 vs LLM 自评
   - ADR-002 状态机 vs ReAct 自由循环 / 多 Agent / 纯 DAG
   - ADR-003 Verifier 先于生成（"先造尺子再造产品"）
   - ADR-005 Fail Closed
   - 这些不是孤立决策，是一条贯通的信任边界哲学：所有信任判断下沉到确定性代码，LLM 不参与"是否可信"的裁决。

3. **追加式 ADR + 真值优先级声明**
   - 个人项目里罕见的工程成熟度信号。`DECISIONS.md` 本身就是"为什么不用 X"问题的现成素材库。

---

## 二、会在面试里翻车的硬伤（按严重度排序）

1. **自己定的量化目标（GOAL.md 第 4 点）一个真实数字都没有** ⚠️ 最致命
   - citation accuracy / hallucination rejection rate / latency / cost —— GOAL.md 全写了，代码里全没有。
   - 致命在于是**自己立的 flag 没兑现**。被问"拦截率多少"只能答"机制上能拦"，答不出"我在 N 个 case 上测到 X%"。

2. **从没用真实 LLM 端到端跑通过**
   - `openai_provider.py` 还没 commit，只有 mock 测试。
   - 拿不出一份真实生成的周报。"我设计了" vs "我跑通了"在大厂是两个量级。

3. **文档开了 5 个 Verifier 的支票，代码只兑现 1 个**
   - `verifier_rules.md` 详述 Citation / Support / Task Schema / Trace / Contract 五层，代码只有 `CitationVerifier`。
   - 观感问题：文档在替代码吹牛。读文档再读代码的面试官会立刻发现落差。

4. **技术深度天花板偏低（视目标岗而定）**
   - 核心 Verifier 是 65 行字符串匹配。correctness 维度漂亮，但没有并发 / 异步 / 持久化（EvidenceStore 是个 dict）/ scale 故事。
   - 面 infra/性能向岗位会被问穿；面 correctness/agent 应用向岗位问题不大。

---

## 三、模块四层拆解（面试话术底稿）

每个模块能讲清楚：是什么/定位 → 为什么这么设计（含否决的候选方案）→ 用了什么技术 → 效果量化。

### 模块 1 · Mission Contract（任务边界契约）
- **是什么**: `contracts/__init__.py`。定义单次 run 的硬边界（goal / workspace_root / allowed_tools / max_steps / token_budget / time_budget / allowed_artifact_types / forbidden_actions）。权限最小化 + 资源配额层。
- **为什么**: 否决"让 Agent 自己判断该不该越权"——把安全边界交给 LLM 等于没有边界（可被 prompt injection 说服越权）。边界下沉为运行前置的确定性校验。
- **技术**: Pydantic v2 + `field_validator` 对 workspace_root 做 `resolve()` normalize。
- **量化**: 路径越界抛 `PermissionError`（`workspace/tools.py:12-20`）。诚实点: 该文件覆盖率 62%，边界测试还不够，是可改进项。

### 模块 2 · Agent Runtime（状态机执行引擎）
- **是什么**: `runtime/runner.py:Runtime.execute()`。控制流拥有者，驱动 PENDING→PLANNING→RETRIEVING→SYNTHESIZING→VERIFYING→(REVISING→VERIFYING)*→PASSED/FAILED。
- **为什么** (ADR-002): 否决 ReAct 自由循环（成本不可控、终止性无保证）、多 Agent（复杂度过高）、纯 DAG（无法表达 verify 失败回环）。状态机同时满足"确定性可控"和"支持有限回环"。
- **技术**: 显式 Enum 状态机；verify-revise 用**有界 for 循环**（`MAX_SYNTHESIS_ATTEMPTS=3`）而非 while True；失败时把 Verifier 具体错误格式化成反馈 prompt 喂下一轮，而非盲目重试。
- **量化**: 循环上限 3（可讲的工程数字：3 次内反馈有效应收敛，超过说明反馈无效、再试是烧 token，fail closed 比死循环安全）。真实收敛率未实测。

### 模块 3 · Workspace Tools（沙箱化文件访问）
- **是什么**: `workspace/tools.py`。Agent 唯一触碰文件系统的通道（list_files / read_file / search_text）。
- **为什么**: 防 path traversal。每次访问都 resolve + prefix 校验，而非入口校验一次就假设后续安全（避免 TOCTOU）。
- **技术**: `pathlib.Path.resolve()` 规范化 + 字符串前缀匹配，越界抛 `PermissionError` 由上层转成结构化失败。

### 模块 4 · Evidence Extraction + Store（证据抽取与证据库）
- **是什么**: `EvidenceExtractor`(`evidence/extraction.py`) + `EvidenceStore`(`evidence/store.py`)。retrieval 与 generation 之间的可信边界。
- **为什么**: candidate（LLM 产出，可能编造）与 evidence（通过四道校验、持久化）是两个信任等级。**这是"为什么不用向量 RAG"的核心**：RAG 解决相关性检索，不解决"内容是否真实存在于源文件此处"。evidence 层做的是**存在性证明**，不是相似度。
- **技术**: 字符串精确匹配 + 行窗口校验，刻意不用 embedding/向量库——对"每条结论可回溯原文"的场景，字符串匹配充分且更可控。
- **量化**: extraction 83% / store 87% 覆盖率；malformed json / 空内容 / 无效类型均有专门测试。真实 discard rate 未实测。

### 模块 5 · Synthesizer（生成 + verify-revise 反馈闭环）
- **是什么**: `synthesis/synthesizer.py`。基于 Evidence Store 生成 weekly_report.md / risks.json / action_items.json。
- **为什么** (ADR-005): system prompt 显式要求"无证据输出 `No data. [unknown]` 不编造"——生成阶段就降低幻觉概率，Verifier 是最后闸门不是唯一防线。`feedback` 参数把上轮具体错误拼进下轮 prompt；StubProvider 显式不支持反馈（走确定性模板）。
- **技术**: 三个独立 system prompt；JSON 输出 `_parse_json_safe` 剥离代码块 + 容错 fallback 空结构不中断 pipeline。
- **量化**: 覆盖率仅 59%（真实 LLM 分支大量未覆盖）。诚实点: 核心信任边界覆盖率高，但端到端真实生成路径无集成测试——是明确的下一步。

### 模块 6 · Citation Verifier（确定性幻觉拦截）
- **是什么**: `verification/citation_verifier.py`。全项目护城河。校验 `[E-XXXX]` / `source_refs` 是否指向真实存在且内容匹配的证据。
- **为什么** (ADR-001/003): 否决 LM-as-judge——自评不可靠（模型为自己输出辩护），且叠加而非消除不确定性。确定性检查边界窄但 100% 可复现。ADR-003: Verifier 先于真实 LLM 做，因为"先生成后验证则中间产出质量无法度量"。
- **技术**: 正则抓 markdown 引用 + 递归遍历 dict/list 抓 JSON source_refs；三层校验（存在 → 可读 → quote 匹配且落在行窗口）；行号不匹配降级为 warning 而非 error（内容漂移比完全捏造轻）。
- **量化**: 覆盖率 97%，全项目核心模块最高。机制上可 100% 拦截字符串不存在的引用（确定性，非概率）；语义支撑校验（Support Verifier）仍在 roadmap。

### 模块 7 · Trace Journal（可审计追踪）
- **是什么**: `trace/journal.py`。append-only 事件日志。
- **为什么**: 定位是"产品能力，不只是 debug log"——要能回答"第 3 条结论为什么被判失败"这类业务问题，故用结构化事件（event_type + data）而非自由文本。`docs/pipeline-viz.html` 消费它做可视化。
- **技术**: 纯 dict + JSON，`_sequence` 自增保序，无第三方依赖（未用 OpenTelemetry——MVP 阶段自定义事件模型更轻且贴合 Verifier 消费需求）。
- **量化**: 覆盖率 100%（模块小，数字本身意义有限，重点讲它如何被 Contract/Trace Verifier 消费）。

### 模块 8 · Provider 抽象层（多模型适配）
- **是什么**: `providers/base.py`(ABC) + `registry.py`(路由) + `openai_provider.py`(唯一真实实现)。
- **为什么**: 否决给每家模型写独立类——DeepSeek/Qwen/GLM/百炼 全部 OpenAI 兼容，只差 base_url/model。写 5 份雷同 HTTP 是重复劳动。识别接口共性、拒绝为表面差异过度抽象。
- **技术**: `openai` SDK；结构化输出走"prompt 塞 JSON Schema + json.loads + pydantic validate"而非原生 function calling（保证跑在不保证支持 tool use 的国产兼容接口上）；重试 1 次后抛明确错误。
- **量化**: 1 份实现覆盖 5 个厂商（5x 复用）；覆盖率 85%，8 个针对性单测。诚实点: base.py 还没有 token/cost 字段，延迟成本数据无——是可观测性那条的未完成部分。

### 模块 9 · 文档分层 + ADR（工程素养，非代码）
- 三层文档（STATUS 动态 / DECISIONS 追加式 ADR / design 稳定）+ 真值优先级声明。
- 否决单一大 README / Wiki（更新频率诉求冲突、维护成本不匹配）。
- 诚实点: STATUS.md 显示"Phase 2 待启动"，实际 git log 显示 Phase 2/3 已完成、Phase 4 未提交——正好验证"文档会滞后，以代码为准"。讲进度时看 git log 和测试，不照抄 STATUS.md。

---

## 四、量化清单（严格区分 已实测 / 未验证）

### 现在就能报的真实数字
- 23 个测试全过，整体覆盖率 76%
- 核心信任边界模块覆盖率显著高于均值: citation_verifier 97% / evidence store 87% / providers base 100% / trace 100% / artifacts 100%（体现"核心路径优先"策略）
- 1 份 OpenAIProvider 复用支撑 5 个模型厂商
- verify-revise 循环上限 3 次（有界，非自由循环）
- 30 个 Python 源文件，src+tests 约 2200 行

### GOAL 写了但还没做出数字的（面试只能说"下一步"，不能说"已做到"）
- citation accuracy / hallucination rejection rate / evidence coverage —— 需真实 LLM + 人工标注 golden answer 的 eval harness
- 端到端延迟 / token 成本 —— trace 里还没埋点
- Support Verifier（LLM-judge 语义支撑）—— roadmap

> 能清楚区分"实测的"和"设计目标还没验证的"，本身是加分点——恰好也是项目自身的哲学（fail closed，宁可 unknown 不编造）。

---

## 五、行动路径（按投入产出比排序，不在多在精）

### 必做（把"证明它真的能工作且有数字"这一个缺口补死）

- [ ] **① 用真实 LLM 跑通一次，把产物 commit 进 repo**
      DeepSeek 便宜。真实 workspace → 真实周报存进 repo。
      杠杆最高：把项目从"我设计了"变成"我建了并跑了"。

- [ ] **② 极小 eval harness：3 个 fixture + 手标 golden citations，跑出 citation precision/recall**
      哪怕只有 3 个 case，就有了第一个**真实数字**，直接兑现 GOAL 第 4 点。
      3 个精心设计的 case > 30 个随便凑的。

- [ ] **③ 一个故意注入的幻觉 case，让 Verifier 当场拦下并报精确位置**
      "钱景 demo"：一句话镇场——"这条 `[E-0099]` 是我伪造的，看 Verifier reject 它"。

- [ ] **④ trace 里加 token / 延迟埋点，运行结束输出成本摘要**
      很便宜，兑现"可观测性/成本摘要"那条。

### 视目标岗决定（面 infra/性能向才做）

- [ ] **⑤ 并发：多文件 evidence 抽取并行 + provider 异步**
      抽取循环天然可并行，改动小，能给出"延迟从 X 降到 Y"的数字。
      面 correctness/agent 应用向岗位可后置。

### 明确不做（陷阱）

- ❌ **别加 SQLite/SQLAlchemy** —— in-memory dict 对单次短生命周期 run 足够。讲法："MVP 刻意用内存，持久化是 scale 时才需要"——这本身是个取舍答案。
- ❌ **别补齐全部 5 个 Verifier** —— 把 `verifier_rules.md` 改成"已实现 / roadmap"两栏。1 个做到 97% 覆盖 > 5 个半成品，也符合自身哲学。
- ❌ **别做多 Agent、别做 Web UI** —— GOAL 已列为非目标，坚持住。

---

## 六、下一步落地顺序建议

1. 先做 ①（真实跑通 + commit）—— 解锁后面所有"有数字"的动作
2. 再做 ③（幻觉注入 demo）—— 复用 ① 的运行环境，成本极低，故事价值最高
3. 然后 ②（eval harness）—— 有了 golden answer 就有 precision/recall
4. ④（埋点）随手做
5. 同步把 `verifier_rules.md` 和 `STATUS.md` 改成诚实的"已实现/roadmap"两栏，消除文档吹牛观感

完成 ①②③ 后（约 2-3 天），项目从"漂亮骨架"变成"精而实的作品"。
