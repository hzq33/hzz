# 结构化世界知识建表 + LLM 自查询工具 + 判断回收评估设计（v5）

> 日期：2026-08-10 · 状态：待评审
> 用户思路演进：
> ① 建表 + LLM 自查询工具（上下文零污染，agentic RAG）
> ② **判断回收（用户澄清）**：LLM 后处理**仍然要判断**（相关性/价值判断不能省，否则无法
>    确保质量）——回收的是**判断过程本身产生的信息**：哪些片段被判相关/不相关、工具结果
>    有没有价值、最终能否回答用户问题。这些判断结果**回收为评估项**，度量"当前数据的
>    相关性是否足够解决用户问题"，且为后续检索/工具改进提供数据依据。
> 关键修正：v4 误解为"运行时不做 LLM 判断"——错。正确理解：**判断照做，判断结果回收
> 为指标**（不是丢弃，也不是省掉）。

---

## 1. 为什么这个思路是对的（对比）

| 方案 | 上下文污染 | 质量保障 | 可观测性 |
|------|-----------|---------|---------|
| v1 注入摘要 | 高 | 无（幻觉直接进上下文） | 无 |
| v2 结构化召回通道 | 中 | 中（原文+标注） | 无 |
| v3 LLM 后处理 | 低 | 高（逐片段判断） | **无（判断结果丢弃）** |
| **v5 工具自查 + 判断回收** | **零（只注入查询结果）** | **高（后处理判断保留）** | **高（判断回收为评估项）** |

v3 的缺陷不是"判断本身"，而是**判断结果用完即弃**——不知道检索/工具到底表现得怎么样。
v5 保留判断，同时把判断结果变成可度量的评估数据。

---

## 2. 判断回收设计（本次定稿核心）

### 2.1 后处理判断（保留，照常做）

LLM 后处理对两路结果做判断（确保质量，不省）：

| 判断 | 输入 | 输出 | 用途 |
|------|------|------|------|
| 召回原文相关性 | novel_search 召回片段 × N | 每片段 相关/不相关 + 理由 | 过滤噪声，只注入相关原文 |
| 工具结果价值 | world_knowledge 返回行 × M | 有价值/无价值 + 是否含答案线索 | 决定工具结果是否注入、是否还需 novel_search 补证据 |

### 2.2 判断回收（新增，核心）

**不丢弃判断结果，回收为指标**。每个判断产生一条观测记录，聚合为评估项：

```
回收项（Prometheus Counter/Histogram，挂现有 metrics.py）：
- retrieval_relevance_total{verdict: relevant|irrelevant}        # 召回片段判断结果
- retrieval_relevance_ratio                                          # 相关率 = relevant/(relevant+irrelevant)  ← 核心评估项
- tool_value_total{query_type, verdict: valuable|useless}           # 工具查询判断结果
- tool_value_ratio{query_type}                                       # 工具价值率 ← 核心评估项
- answer_coverage_total{verdict: answerable|unanswerable}            # 最终能否回答用户问题
```

**评估项的意义**：
- `retrieval_relevance_ratio` = "检索到的内容是否相关" 的在线度量（替代/补充离线 eval 52 case）
- `tool_value_ratio` = "结构化数据是否真能解决用户问题" 的度量（验证建表+工具的价值）
- `answer_coverage` = 端到端"检索+工具能否覆盖用户需求"

**消费方式**：
1. `/metrics` 暴露（Prometheus 抓取，现有端点）
2. 定期/按需查看：如果 relevance_ratio 低 → 检索质量有问题（查 rerank/embedding）；
   tool_value_ratio 低 → 工具参数设计/表数据有问题（查 SQL 匹配/字段）
3. 对比基线：改造前后 relevance_ratio 变化 = 改进效果的数字证据（对应你第 2 点"先验证再改"）

### 2.3 与离线评估的关系

| 评估 | 数据源 | 频率 | 用途 |
|------|--------|------|------|
| eval_dialogue（离线） | 52 手工 case | 改造时跑 | 精确对比，回归门禁 |
| **判断回收（在线）** | **真实用户查询 + LLM 判断** | **持续** | **监测真实相关性漂移，发现检索/工具问题** |

两者互补：离线评估管"改得对不对"，在线回收管"日常跑得好不好"。

---

## 3. 建表设计

### 3.1 数据源 → 表

现有 4 个数据源（story_analyses/*.json 的 relations/events、timelines/*.json、
lorebooks/*.json、graphs/*.json），全部已有结构化字段，**无需重新生成**，只需建索引表。

| 表 | 来源 | 行数（史莱姆实测） | 关键列 |
|----|------|------------------|--------|
| `world_relations` | story_analyses relations | 22 | source, target, relation_type, polarity, confidence, chapter_order, doc_id, chapter_title, story_time.period, evidence_ids[] |
| `world_events` | story_analyses events | 29 | summary, event_type, characters[], confidence, chapter_order, doc_id, chapter_title, story_time.period, evidence_ids[] |
| `world_timeline` | timelines chronicle | 29 | seq, summary, event_type, characters[], doc_id, chapter_order, story_time.period |
| `world_lorebook` | lorebooks entries | 32 | entity, keys[], kind, time_range, seq_from, seq_to, priority, content(仅定位用) |
| `world_character_events` | timelines by_character | 27 角色 | character, seqs[] |

### 3.2 存储选型

**SQLite 最合适**（对比）：
- 查询是**精确过滤**（source=X AND relation_type=Y），不是向量相似度 → SQL 天然匹配
- 数据量小（每系列几十行）→ 无性能问题
- 无新依赖（Python 内置 sqlite3）
- 每行带 series_id 列，按系列过滤

```
data/world_kb.sqlite
  tables: world_relations / world_events / world_timeline / world_lorebook
  每行带 series_id 列，按系列过滤
```

### 3.3 构建时机

- 懒构建：工具首次查询某系列时，若表无该系列数据 → 从 JSON 源构建
- 或 `scripts/dev/build_world_kb.py` 全量构建
- 老系列无 JSON → 表空 → 工具返回"该系列无世界知识数据"

---

## 4. 工具设计

### 4.1 单一工具 `world_knowledge`（参数化查询）

```
name: world_knowledge
description: 查询系列的结构化世界知识（角色关系/事件/时间线/设定）。
  当用户问题涉及"谁和谁什么关系""后来发生了什么""某个时期""某某是谁/是什么"
  时调用；返回精准条目+原文证据定位，不返回大段原文。

参数:
  query_type: enum[relations, events, timeline, lorebook, character_events]
  series_id: str            # 必填，当前作品
  entity: str | None        # 角色名/实体名
  entity2: str | None       # 关系对第二方（relations 用）
  relation_type: str | None # 关系类型过滤（敌对/情侣/朋友…）
  era: str | None           # 时代过滤（转生前/转生后…）
  limit: int = 10
```

### 4.2 返回形态

```
ToolResult: 结构化行列表（cap 10 行），每行含：
  - relations:  source —[relation_type/极性]→ target（章节, 时代）
  - events:     [时代] summary（章节）→ 附 evidence block_id 1 个
  - timeline:   #seq [时代] summary（章节）
  - lorebook:   entity: content 摘要前 100 字（仅定位）
  - character_events: 角色 seq 列表 → 对应事件 summary
每行末尾附 "证据: {block_id}"（不展开原文，需要原文时 LLM 再调 novel_search）
```

两层分工：
- world_knowledge = 世界知识的"目录"（谁-谁-何时-何地-何事）
- novel_search = 原文的"全文"（证据片段）
- 后处理判断 = 把关两路结果质量，**判断结果回收为评估项**（§2）

### 4.3 注册与接入

- src/tools/builtin_world_knowledge.py（新，BaseTool 子类，同 novel_search 模式）
- config.yaml tools.builtin 列表追加 world_knowledge
- 扮演 + 通用 chat 都可见（工具列表自动暴露）
- 查询实现：application 层 `world_knowledge_service.query(...)`（薄封装 SQLite 读，
  复用 services 层模式，routers/tools 不直连 domain）

---

## 5. 知识边界（你的第 4 点，与工具结合）

工具本身**返回全量**（LLM 决定查什么）。知识边界由 **LLM 在回答时裁决**：
- 工具返回了"雷姆喜欢菜月昴"（关系行）
- 扮演雷姆：LLM 知道这是 own（亲身体验）→ 答
- 扮演艾米莉亚问雷姆私事：LLM 判断 heard/unknown → 不答或"我不清楚"

知识边界不写在工具里，写在扮演 system prompt（增强现有约束：
"角色只知道与自己相关/听说的；工具查到的信息若角色不该知道，表示不知道"）。
LLM 有工具结果 + 角色卡背景 → 自主裁决。

---

## 6. 与现有机制的关系

| 现有 | v5 |
|------|-----|
| novel_search（向量原文检索） | 保留；world_knowledge 查目录，novel_search 取证据原文 |
| metrics.py（Counter/Histogram/observe_rag_fallback） | **扩展**：新增回收指标（§2.2），挂同一 /metrics |
| eval_dialogue | 保留离线评估；回收项是持续在线评估，互补 |
| `_graph_context` 富集 | 保留兜底 |
| lorebook 扮演 era 注入 | 保留（system prompt 层）；world_knowledge 是主动查询层 |

---

## 7. 实施步骤

### 阶段 1：建表 + 服务
- `world_knowledge_service.py`：从 JSON 源构建 SQLite（懒构建），查询封装
- `scripts/dev/build_world_kb.py`：全量构建现有系列
- 验证：史莱姆系列 → relations 22 / events 29 / timeline 29 / lorebook 32 / character_events 27

### 阶段 2：工具
- `builtin_world_knowledge.py`：BaseTool 子类，5 种 query_type
- config.yaml 注册；扮演/chat 工具列表验证

### 阶段 3：LLM 后处理判断 + 判断回收
- 后处理：对 novel_search 召回片段判相关性、对 world_knowledge 结果判价值（保留判断）
- **回收**：metrics.py 新增 3 组指标（relevance/tool_value/answer_coverage），后处理处上报
- 验证：/metrics 能看到指标变化；扮演真实问答后比率更新

### 阶段 4：评估对比
- eval_dialogue 52 case 重跑（关系类 case 用工具后命中率对比基线）
- 观察回收指标：关系类查询的 tool_value_ratio、answer_coverage 变化
- 基线已跑：关系类 case（"你和你妹妹关系怎么样"等）当前未命中（失败列表里）

### 阶段 5：知识边界提示
- 扮演 system prompt 增强（角色只知道相关/听说的）
- 验证：扮演 A 问 B 的私事 → 恰当拒绝

---

## 8. 待评审

1. **存储选型**：SQLite 还是 LanceDB 新表？（我倾向 SQLite：精确过滤、零依赖、量小）
2. **工具粒度**：一个 `world_knowledge` 参数化，还是拆 4 个？（我倾向一个）
3. **构建时机**：懒构建（我倾向）vs 入库钩子增量写
4. **工具返回**：只带 evidence block_id（我倾向）vs 带 snippet
5. **回收指标粒度**：按 query_type 细分（tool_value_ratio{relations/events/...}）还是全局一个？
   （我倾向按 query_type 细分——能定位哪个类型的数据相关性差，驱动精准改进）
6. **后处理判断的 LLM 成本**：判断复用主 LLM 回答（一次调用里先判断再回答）还是独立调用？
   （我倾向合并进主 LLM 回答——后处理判断不做独立 LLM 调用，靠 prompt 让主 LLM 输出判断+回答，省一次调用）

