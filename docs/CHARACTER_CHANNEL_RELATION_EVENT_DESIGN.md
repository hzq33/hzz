# Characters 通道重定位：关系 / 事件轻量检索设计

> 日期：2026-08-04 | 状态：**Implementing（R1+R2 已开工）**  
> 决策动机：现网 Lance `character` 行数 = 0；人设已由 `CharacterCard` JSON 稳住扮演；通道空转却占「人设」语义。  
> 相关：[NOVEL_RAG_DESIGN.md](./NOVEL_RAG_DESIGN.md) · [CHARACTER_ON_DEMAND_DESIGN.md](./CHARACTER_ON_DEMAND_DESIGN.md) · [IMPERSONATION_CITATION_FACT_STYLE_SPLIT_DESIGN.md](./IMPERSONATION_CITATION_FACT_STYLE_SPLIT_DESIGN.md) · story_analysis（`data/story_analyses/`）· CharacterGraph（`data/graphs/`）

---

## 1. 问题与结论摘要

### 1.1 现状

| 组件 | 现状 | 扮演是否依赖 |
|------|------|----------------|
| `CharacterCard` JSON | 性格/口吻/口头禅/样本台词 | **是（主锚）** |
| Lance `block_type=character` | 设计为人设向量；生产常 **0 行** | **否** |
| IntentRouter `character` / 关系问 | 指向空通道 | 工具/通用检索空转 |
| `story_analysis` | events / relations / foreshadows + evidence | 知识库 UI，**未入向量** |
| `CharacterGraph` | 共现/同场边 | ingest 出 character 块才建；现库常无 |

### 1.2 决策（本设计）

**将 `channel=character` 从「人设/说话风格」改为「角色关系与事件轻量索引」**，专门服务：

- 时间线关系（含跨卷演变条目）  
- 双人（多人）共现 / 关系边  
- 轻量事件节点（带角色列表）  
- （可选）伏笔与角色相关的结构化摘要  

**人设与口吻不迁入该通道**，继续由 `CharacterCard` + dialogue 口吻池承担。

一句话：**通道改名不改 Lance 表名亦可——语义从 profile → relation/event graph lite。**

### 1.3 非目标

- 不做完整 GraphRAG / Neo4j 级知识图谱  
- 不取代 narrative Parent/Child 真源  
- 不把人设 traits/台词样本塞回 character 通道  
- 不要求上传热路径必跑全量 story LLM（可按需 build 后索引）  
- 本期不强制改 API 路径名 `/characters`（人设建卡 API 保留）

---

## 2. 为何可以「占坑」而不冲突人设

| 维度 | 人设档案 | 关系/事件索引（新 character 通道） |
|------|----------|-------------------------------------|
| 问题 | TA 怎样说话 / 什么性格 | TA 和谁、何时、发生什么 |
| 存储 | Card JSON（已稳） | Lance `character` 通道块 |
| 更新 | 建卡/蒸馏 | story_analysis / 共现统计 |
| 真源 | 归纳即可 | **必须** `evidence_block_ids` → narrative |

二者并存：Card 约束语气；本通道约束「关系事实候选」；最终仍以 narrative expand 举证，避免摘要幻觉。

---

## 3. 目标能力映射

| 用户问法 | 期望命中块类型 | 再接什么 |
|----------|----------------|----------|
| 你和日向后来关系怎么样 | `relation`（带时间/卷） | expand evidence Parents |
| 日向和库洛艾相处怎么样 | `relation` 或 `cooccur` pair | 无边 → 拒答；有边 → 叙事 |
| 第十一卷日向做了什么 | `event` | evidence 原文 |
| 跨卷关系怎么变 | 多条 `relation` 按 doc/chapter 排序 | 时间线拼装，禁止单块瞎编 |

---

## 4. 数据模型

### 4.1 通道约定

- Lance：继续 `block_type = "character"`（少迁表）；用 **`granularity` / `subtype`** 区分块类。  
- 向量列：仍用 `vec_character`（语义改为「关系/事件描述文本」）。  
- **禁止**再写入旧式「性格+口头禅」profile 块（或 `subtype=profile` 标记废弃，检索默认排除）。

### 4.2 块类型（subtype）

#### A. `relation` — 关系边 / 关系演变条目

| 字段 | 说明 |
|------|------|
| `global_id` | `{series_id}_rel_{hash(source,target,doc,chapter,type)}` |
| `subtype` / `granularity` | `relation` |
| `characters` | `[source, target, …别名展开可选]` |
| `character_name` | 可空，或填「主视角」名（兼容旧 list_characters） |
| 正文（可放 `personality`/`speech_style` 字段滥用需避免） | 建议扩展：`narrative_text` 或专用 JSON 字段存结构化；**vec 文本**见下 |
| `doc_id` / `chapter_title` / `chapter_order` | 时间定位 |
| `ref` / evidence | `ref_chunk_ids` 或 evidence 列表 → Parent/Child id |
| 业务字段 | `relation_type`, `polarity`, `summary`, `confidence` |

**vec_text_character 示例：**

```text
关系 利姆露 日向 | 类型:对立/战斗 | 极性:negative
摘要: 广范围结界中对峙，日向意图消灭魔物国家
卷:…vol11 章:… 
别名: 利姆路 利姆鲁
```

跨卷演变 = **多条** relation（不同 `doc_id`/`chapter_order`），不是一条「后来变成朋友」糊掉时间。

#### B. `event` — 事件节点

| 字段 | 说明 |
|------|------|
| `global_id` | `{series_id}_evt_{event_id}` |
| `subtype` | `event` |
| `characters` | 参与者列表 |
| vec | `事件 {summary} 角色:A,B 卷章…` |
| evidence | 必填，否则不入索引或标 `low_evidence` |

#### C. `cooccur` — 双人共现轻边（可无 LLM）

| 字段 | 说明 |
|------|------|
| 来源 | Roster / CharacterGraph / 同块 speakers |
| vec | `共现 日向 库洛艾 次数:N 章节:…` |
| 用途 | 「相处/一起」类问的 **门控与召回**；结论仍要 narrative 或更强 relation |

共现 ≠ 关系定性；只回答「有没有同场线索」。

#### D. （可选）`foreshadow`

与角色相关的伏笔摘要 + evidence；权重低于 event/relation。

### 4.3 与现有结构对齐

| 已有 | 映射 |
|------|------|
| `StoryAnalysisSnapshot.relations` → `RelationChange` | → `subtype=relation` |
| `.events` → `StoryEvent` | → `subtype=event` |
| `.foreshadows` | → 可选 foreshadow |
| `CharacterGraph` 边 | → `cooccur` 或补强 relation 无 LLM 时的骨架 |
| `CharacterCard.relationships` 长文本 | **不索引**；避免与证据关系双源 |

---

## 5. 写入管道（设计）

```text
按需: POST story-analysis/build
  → save data/story_analyses/{series}.json
  → index_character_channel(snapshot)   # 新步骤
       删除该 series 旧 subtype∈{relation,event,…} 块（或按 fingerprint）
       为每条 relation/event 生成 NovelBlock + embed vec_character
       upsert Lance

可选轻量（无 LLM）:
  ingest 结束 或 nightly
  → 从 dialogue speakers / narrative 共现 建 cooccur 块
  → 不替代 story relation

废弃:
  generate_character_llm 写入的 profile 式 character 块（默认不再写）
```

**幂等**：`content_fingerprint` + per-volume `stats.volume_fingerprints`；全系列/单卷 rebuild 可区分缓存。  
**单卷 merge**：指定 `doc_id` 时只替换该卷 claims，再与磁盘其它卷合并后落盘，避免抹掉整系列。  
**进度**：异步 Job 带 `progress.{chapter_done,chapter_total,message}`；前端轮询展示。

### 5.1 剧情提取契约（2026-08 重设计 / story_v3 质量修复）

产品目标是 **关系与事件检索索引**，不是书迷 Wiki 三件套：

| 字段 | 默认 | 承重 |
|------|------|------|
| `relations` | 开 | 扮演/检索 **P0** |
| `events` | 开 | 事件问 / UI **P1** |
| `foreshadows` | **关** | 仅可选 UI；不进 character 通道 |

`config.yaml` `story_analysis` 要点：

| 项 | 默认 | 作用 |
|----|------|------|
| `max_tokens` | 4096 | map 输出上限 |
| `per_type_cap` | **3** | 控输出长度，降低 JSON 截断 |
| `summary_max_chars` | 80 | prompt + reduce 双保险截断 |
| `map_retry` | 开；失败时仅 relations 再试 1 次 | 救回截断/解析失败章 |
| `entity_filter.reject_substrings` | 做法/众人/现场/… | 硬拒弱端点 |
| `chapter_quota.balance_by_volume` | true | `max_chapters` 跨卷均摊 |

**截断可观测**：map 走 `achat_result`，`finish_reason=length` ⇒ `stats.truncated_chapters`（`likely_truncated` 同值兼容）。  
**覆盖 stats**：`parse_failures` / `retry_successes` / `chapters_with_claims` / `coverage_ratio` / `dropped_weak_entity` / `chapters_per_doc`。  
**别名**：Roster `aliases_observed` → canonical（无表则跳过）。

验收（同语料 force 重跑）：解析失败率 ≤15%；有效章覆盖 ≥70%；快照无「做法/众人/现场」端点；UI 覆盖 &lt;50% 出警告。

摘要仅作跳板；真源仍是 `evidence` → narrative。UI Tab 文案为「关系与事件」。

**跨卷**：story_analysis 已按 series 聚合 `doc_ids`；relation/event 带各自 `doc_id`，检索可不锁单卷，或 impersonation `doc_id` 过滤时降级为「本卷无演变记录」。

---

## 6. 检索与路由

### 6.1 IntentRouter 调整（设计）

| 意图 | primary | 权重建议 |
|------|---------|----------|
| 性格/人设介绍 | **不再**打空 character；改 Card API / 或 narrative+dialogue | 或显式 `profile` 非通道 |
| 关系/朋友/敌人/相处/后来 | `character`（新语义）+ narrative | 如 0.45 / 0.55 |
| 事件/发生了什么（含人名） | character(event) + narrative | 混合 |
| 模仿口吻 | dialogue（不变） | |

关系问命中 character 块后：

1. 取 `ref_chunk_ids` / evidence → `expand_narrative_hits`  
2. Prompt：**关系摘要仅作线索；以原著参考为准；未写明则不确定**  
3. Citations：fact = narrative；可选另附 `role=relation` 摘要（UI 勿当原文）

### 6.2 扮演路径（设计）

`ImpersonationAgent._retrieve_fact_context` 在事实问且检出 ≥2 实体或关系启发时：

```text
可选先 search(channel=character, filters={characters:[…]})
  → 有高置信 relation/event → 注入「关系/事件线索」+ expand 证据
  → 无命中或 evidence 空 → 维持现 narrative；并加强 NO_FACT / 不确定
```

口吻仍不走本通道。

### 6.3 双人共现策略

1. 解析实体 A、B（含别名）。  
2. 优先检索 `relation`（A∩B）；其次 `cooccur`。  
3. 过滤：块 `characters` 需覆盖 A 与 B（集合包含）。  
4. 仅 A 命中、B 不在块内 → **丢弃或降权**（避免再被「日向」单实体带跑）。

### 6.4 跨卷演变

- 检索 topK 多条 relation，按 `(doc_id, chapter_order)` 排序后拼「演变线索」。  
- 禁止模型把单条对战 relation 说成「后来和好」；prompt 写明「仅陈述已列出线索，不得外推」。

---

## 7. 与 CharacterGraph 的分工

| | Graph 文件 | character 通道向量 |
|--|------------|-------------------|
| 强项 | 精确邻接、路径、共现次数 | 自然语言问句召回 |
| 弱项 | 难直接吃「后来怎么样」 | 不擅长精确拓扑 |

**推荐**：Graph / cooccur 做门控与补边；story relation/event 做语义检索；二者可同时存在。

---

## 8. 忠实度与产品约束

1. **无 evidence 不入检索**（或仅 debug）。  
2. 摘要与原文冲突 → 以 narrative 为准（已有 grounding hint）。  
3. UI：关系块展示为「结构化线索」，徽章主仍 narrative；避免「人设出处」文案。  
4. 外传无后日谈：通道正确为空或仅有对战 relation → 应拒答演变，而不是编同盟。

---

## 9. 兼容与迁移

| 项 | 策略 |
|----|------|
| 旧 profile character 行 | 删除或 `subtype=legacy_profile` 且默认不搜 |
| `list_characters()` | 改为 Roster / Card / 块内 `characters` 字段聚合，不依赖 profile 行 |
| 文档与 OpenAPI 文案 | `character` = 关系与事件轻量索引 |
| 评估夹具 | `expected_channels` 可含 character 当关系问；人设问不再期望 character |
| 建卡 API | 不变；明确「不写 Lance character」 |

---

## 10. 实施阶段（待开工时）

### Phase R0 — 约定与文档（本文件）

- [x] 语义重定位；人设留 Card  
- [ ] 评审：subtype 命名、是否改 channel 字符串（建议暂不改名，改文档）

### Phase R1 — 索引主路径

1. [x] `story_analysis` save 后 → 生成 relation/event 块并 embed（`character_channel_index.index_story_analysis`）。  
2. [x] 删除 series 旧关系块的幂等逻辑（`delete_by_global_ids` + replace）。  
3. [x] 单测：RelationChange → block 字段 / vec 文本含双人名（`tests/test_character_channel_index.py`）。  
4. [x] Lance `style_tags_json` 信封持久化 `relationships` / `ref_chunk_ids`（evidence expand 所需）。

### Phase R2 — 检索主路径

1. [x] IntentRouter 关系问权重；双实体覆盖过滤（检索 + 扮演）。  
2. [x] NovelRetrieval / 扮演 fact：命中后 expand evidence；无线索则 NO_FACT。  
3. [ ] 验收用例：日向关系问 → 若仅有对战线索则答对战+不确定后来；双人无共现则明确未写（需真实语料手工验）。

### Phase R3 — 共现与跨卷

1. ingest 后写 `cooccur` 块（无 LLM）。  
2. 多 relation 时间线拼装。  
3. 可选 Graph 与通道对齐校验。

### Phase R4 — 清理

1. 移除/停用 ingest `generate_character_llm` 写 profile 块。  
2. Eval 与前端文案同步。

---

## 11. 风险

| 风险 | 缓解 |
|------|------|
| story_analysis 本身幻觉 | 强制 evidence；低置信不索引；人工可重跑 |
| 通道名 `character` 误导 | 文档/UI 改称「关系与事件」；长期可 alias `relation` |
| 与 Card.relationships 双源 | Card 侧改为短列表或「详见剧情分析」，或生成时忽略卡内关系长文 |
| 空分析时通道仍空 | cooccur 兜底 + 明确降级文案 |
| 扮演延迟 | 仅关系启发时查 character；top_k 小 |

---

## 12. 验收标准（设计层）

| ID | 标准 |
|----|------|
| A1 | 人设扮演不依赖本通道；无卡则仍失败于 Card，而非 character 检索 |
| A2 | 关系问可命中带 evidence 的 relation/event；citations 主为 narrative |
| A3 | 双人问要求块内双实体覆盖 |
| A4 | 无后日谈证据时不把对战块说成「后来同盟」 |
| A5 | 旧人设向量不再污染检索 |

---

## 13. 决策摘要

| 决策 | 选择 |
|------|------|
| 人设 | 留 `CharacterCard`，不进 Lance character |
| character 通道 | 改为关系/事件/共现轻量检索 |
| 真源 | narrative（evidence expand） |
| 主数据源 | `story_analysis` + 可选 Graph/共现 |
| 实现 | **R1+R2 代码已接线**；R3 共现 / R4 清理待做 |

---

## 14. 详细调研 · 可行性 · 利弊（2026-08-04）

### 14.1 业界对标（与本方案的距离）

| 范式 | 做法 | 与本项目提案的关系 |
|------|------|-------------------|
| **VectorRAG only** | 纯 Child/段落向量 | 你们 narrative 已是；弱于关系/多跳 |
| **GraphRAG / KG** | 抽实体关系图再遍历 | CharacterGraph + story relations 是 **简化版**；本方案再 **向量化边/事件** |
| **HybridRAG** | 图结构 + 向量并行，合并上下文 | 最接近目标形态：character 通道≈「可语义检索的边/事件」，narrative≈原文向量 |
| **LlamaIndex Parent/Child** | 小块检索大块举证 | 已有；解决语境不解决关系演变 |
| **CRAG / Self-RAG** | 检索后打分、拒答、改写 | **正交必做**；与通道改造叠加才压幻觉 |
| **NovelHopQA** | 长篇多跳失败模式 | 说明：缺跳时模型会自信错答——关系索引旨在补「跳」的线索，不是保证全对 |

HybridRAG 类研究常见结论（摘要）：

- 图/结构侧抬高 **faithfulness**、关系类问更稳；  
- 纯向量 recall 广但易「相关不支撑」；  
- 混合后 **context precision 可能下降**（上下文变杂）→ 必须 **evidence 门控 + 双实体过滤 + 摘要降权**。

**对本项目的启示**：不要做成「只检索关系摘要就生成」；必须 **摘要召回 → 原文 expand → 生成**，否则忠实度可能不如现在「乱召回但对战原文至少在场」。

### 14.2 与「剧情分析」结合的可行性

#### 已具备（高可行）

| 条件 | 证据 |
|------|------|
| 结构化三件套 | `events` / `relations` / `foreshadows` 已落盘 JSON |
| 证据约束 | `_reduce_snapshot`：**无 evidence 的条目直接丢弃** |
| 卷章定位 | `doc_id` + `chapter_order` + `chapter_title` |
| 产品入口 | Knowledge「剧情分析」build/GET 已通 |
| 空通道可占 | Lance character 现为 0 行，迁移成本低 |
| 人设已旁路 | 扮演不依赖 character 通道 |

#### 缺口（中等风险，可设计消化）

| 缺口 | 影响 | 缓解 |
|------|------|------|
| 分析按需、现库常无 snapshot | 通道常空 | UX：引导先跑分析；cooccur 无 LLM 骨架 |
| Map 每章 cap（如 relations≤8） | 关系召回不全 | 可调 cap；或二次定向补抽 |
| LLM 摘要质量波动 | 错关系进索引 | 低置信过滤；生成仍以原文为准；可重跑 force |
| 别名未归一 | 「利姆路/利姆露」双实体过滤失败 | 索引时用 AliasMap 展开 `characters` |
| Graph 与 analysis 双源 | 共现边无定性 | cooccur 只做门控，不定性「朋友」 |
| fingerprint 重建 | 旧块残留 | 按 series 删旧 subtype 再写 |

#### 技术可行性评分（主观）

| 维度 | 分（1–5） | 说明 |
|------|-----------|------|
| 数据模型契合 | 5 | RelationChange/StoryEvent 几乎 1:1 可投影 |
| 存储/嵌入复用 | 5 | 现成 `vec_character` + block_type 过滤 |
| 与剧情分析结合 | 5 | build 后钩子即可 |
| 检索接线工作量 | 4 | Router + 扮演 fact 分支 + 过滤 |
| 质量闭环（拒答/校验） | 3 | 通道 alone 不够，需 P0 grounding |
| 全量自动化（上传即有） | 2 | 依赖 LLM 成本；不宜默认热路径全跑 |

**总判**：作为「剧情分析的检索投影」**可行且契合度高**；作为「替代 narrative 的事实引擎」**不可行**。

### 14.3 对项目的利

1. **激活死通道**：Intent 关系问不再空转；四通道名副其实（语义更新后）。  
2. **对准真实痛点**：扮演「后来关系 / 双人相处」缺的是结构化跳板，不是更大 Child。  
3. **复用已有投资**：剧情分析、evidence 绑定、系列级 JSON、Knowledge UI 全部可延续。  
4. **人设解耦更清晰**：Card = 口吻；character 通道 = 关系/事件；减少 Card.relationships 静态句干扰的动力。  
5. **跨卷天然**：series snapshot 多 `doc_id`，比单卷 narrative topK 更适合「演变」。  
6. **可渐进**：R1 只索引不接线扮演 → R2 再进 impersonation；失败可关钩子回滚。  
7. **对齐 HybridRAG 方向**：低成本拿到「结构召回 + 原文生成」的骨架，无需上 Neo4j。

### 14.4 对项目的弊与成本

1. **幻觉换皮风险**：若只喂关系摘要、弱化原文，会出现「索引里写了就当成书」——比现在更危险。  
2. **依赖用户跑分析**：外传 15 卷全量 map 有 **LLM 费用与时延**；未跑则关系问仍空。  
3. **维护双读模型**：JSON（浏览）+ Lance（检索）需指纹同步，否则 UI 与检索不一致。  
4. **命名债务**：`character` / `/characters` 建卡 API 与「关系通道」同名，文档与前端易混。  
5. **Eval/夹具**：`expected_channels`、工具文案、监控标签要改，短期 CI/文档噪声。  
6. **context 变杂**：Hybrid 常见 precision↓ → prompt 与 citations 必须区分「线索 vs 原文」。  
7. **无法创造语料**：外传无后日谈时，正确结果仍是拒答；通道不能「造出」正文没有的和解。

### 14.5 利弊对照（决策用）

| | 做（分析→索引→检索） | 不做（维持现状） |
|--|---------------------|------------------|
| 关系追问 | 有望「有线索则有原文、无线索则拒答」 | 继续对战块+编后日谈 |
| 人设 | 更干净（通道不再装人设） | Card 已够，无影响 |
| 复杂度 | +索引钩子与过滤 | 低 |
| 成本 | 分析 LLM（已有）+ 少量 embed | 零增量 |
| 风险 | 摘要滥用 | 检索错配+编造（已发生） |

**建议**：值得做，但范围锁死为 **「剧情分析的检索投影 + 原文强制 expand」**；同步做实体过滤与证据不足拒答，否则弊可能大于利。

### 14.6 推荐落子顺序（仍不改代码，仅优先级）

1. 产品：剧情分析完成后提示「可用于扮演关系检索」（心理模型）。  
2. 工程：`save_analysis` → upsert relation/event 块（R1）。  
3. 检索：双实体覆盖 + evidence expand（R2）。  
4. 扮演：关系启发时查通道；无则 NO_FACT（R2）。  
5. 可选：ingest cooccur；Graph 对齐（R3）。  
6. 并行：生成后 grounded 检查（与通道独立，强烈建议）。

### 14.7 一句话结论

与剧情分析结合 **高度可行、利大于弊的前提是「索引线索、原文裁决」**；若做成「只检索关系摘要」，则对项目是净弊。

