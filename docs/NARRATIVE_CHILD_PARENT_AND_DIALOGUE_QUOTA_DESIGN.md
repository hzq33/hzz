# Narrative Child→Parent + Dialogue 配额分层 修改方案

> 状态：**Current（主路径已落地）** | 同步 2026-07-28  
> 范围：① Narrative 检索/举证拆分；③ Dialogue 够用即止（少正则）  
> **不变**：② Character Inventory → 知识库候选角色列表（CLUENER + 聚类 + LLM 归一）  
> 相关：`CHARACTER_ON_DEMAND_DESIGN.md`、`CHARACTER_INVENTORY_DESIGN.md`、`DIALOGUE_CHAPTER_EXTRACT_DESIGN.md`、`NOVEL_RAG_DESIGN.md`；早期总案见 `archive/NOVEL_INDEX_FIRST_DESIGN.md`  
>
> **已落地**：Dialogue `mode=quota`；Narrative Phase B 邻窗；**Phase C 真 Parent/Child 切块**（`HierarchicalChunker`，Child 向量 / Parent 举证，`index_parents` 可选双粒度）；Lance 持久化 hierarchy；检索过滤 Parent 行  
> **2026-08-09 取用端修复**：`lance_backend.get_block/get_blocks` 原用零向量向量搜索取行，Parent 举证块（全零向量）不在 ANN 索引可达范围 → 永远取不到，3.4 的展开实际失效（fallback 到命中 Child）。已改为**纯 SQL filter 按 id 直查**，Child 命中 → Parent ± 邻居举证生效（单测 `tests/test_narrative_parent_child.py`）。  
> **可选后续**：Parent 级 rerank、按卷字数自适应配额

---

## 1. 背景与决策摘要

### 1.1 问题

| 问题 | 现状 | 影响 |
|------|------|------|
| narrative 同粒度既检索又举证 | ~500 字块 `vec_text = narrative_text` | 点查精度不足；跨句语境偶发断裂 |
| dialogue 接近全章窗抽取 | `cloud_chapter` 顺序扫窗至 `max_calls` | 二十卷成本高；尾部章易饿死；收益递减 |
| 说话人需叙事上下文 | 已验证「只传纯对话」不准 | 必须保留 LLM + 章/窗叙事；**不**回切「正则 Span + 短窗猜名」主路径 |

### 1.2 决策（一句话）

- **Narrative**：小 Child 负责命中，大 Parent（邻域）负责原文证据。  
- **Dialogue**：保持章窗 LLM 抽+归因；按 Inventory 档位做**配额**，够用即停；不足再按需补抽。  
- **Inventory**：知识库勾选列表与正名/别名**不动**；仅继续向 Dialogue 提供 `volume_seed` / importance。

### 1.3 非目标

- 切回 `legacy_window`（规则全文 Span + 短窗分类）为默认  
- 用正则做说话人主引擎  
- 上传时批跑全角色人设 / 全书 QA  
- 本期上 Late Chunking / 换向量库  
- 改动 Inventory 的 NER 管线与候选列表生成逻辑  

---

## 2. 改版后全流程

```text
上传一卷（EPUB/TXT/MD）
  │
  ├─ 0  转换 / 分章 → NovelDocument；series_id + doc_id
  │
  ├─ ① Narrative（改）
  │     章文本 → Parent 块（原文真源，可弱/不向量）
  │            → Child 块（仅 Child 写 vec_narrative）
  │
  ├─ ② Character Inventory（不变）
  │     CLUENER → 聚类 → LLM 归一
  │     → Roster 展示候选 / AliasMap
  │     → volume_seed + importance 供 ③
  │
  ├─ ③ Dialogue（改：配额分层）
  │     provider 仍为 cloud_chapter 系（LLM + 章叙事）
  │     L0 过滤 → L1 有预算覆盖抽 → L2 配额验收 → 索引
  │     （L3 按需补抽在建卡/扮演触发，见 §5）
  │
  └─ 索引落库 + 更新 Roster 统计（dialogue_count 等）
         │
         ▼
  ④ 按需：勾选建卡 / 扮演 / 剧情分析
       事实检索走 Child→Parent expand
       口吻检索走 dialogue（配额样本）
```

---

## 3. Narrative：Child → Parent

### 3.1 目标

| ID | 目标 |
|----|------|
| N1 | 检索命中粒度更小、更聚焦（Child） |
| N2 | 喂给 LLM / 引用的原文保持完整语境（Parent ± 邻域） |
| N3 | Parent 仍是事实真源；人设/剧情摘要不得覆盖 |
| N4 | 兼容现有 `get_block` / QA `ref_chunk_ids` / story-analysis 证据字段 |

### 3.2 数据模型

#### Parent（举证单元）

复用/扩展现有 narrative `NovelBlock`：

| 字段 | 说明 |
|------|------|
| `global_id` | 现格式：`{doc_id}_c{ccc}_n{nnnn}` |
| `block_type` | `narrative` |
| `narrative_text` | 父块全文（约 600–1200 字，或维持现 ~500 起步） |
| `vec_text_narrative` | **空**（默认不入向量；可选 `index_parents: false`） |
| `token_length` / `chapter_title` / `doc_id` / `all_person` | 沿用 |
| 新增 `granularity` | `"parent"` |
| 新增 `child_ids` | 可选，子块 id 列表（调试用） |

#### Child（检索单元）

| 字段 | 说明 |
|------|------|
| `global_id` | `{parent_id}__s{iii}` |
| `block_type` | `narrative`（同通道，靠 metadata 区分）或引入 `narrative_child`（**推荐同通道 + granularity**，少改 IntentRouter） |
| `narrative_text` | 子段文本（约 120–200 字，句边界优先） |
| `vec_text_narrative` | = `narrative_text`（可加章名前缀） |
| 新增 `granularity` | `"child"` |
| 新增 `parent_id` | 指向 Parent `global_id` |
| 新增 `char_start` / `char_end` | 相对 Parent 或章内偏移（举证高亮可选） |
| 新增 `prev_id` / `next_id` | 同 Parent 内兄弟；跨 Parent 邻接可选 |

Lance / 序列化：在 `NovelBlock.to_dict` 与 Lance row 增加可空列或塞进现有 JSON 扩展字段（迁移策略见 §7）。

### 3.3 切块规则

```text
章文本
  → 按段落/空行聚成 Parent（目标 parent_chars，默认 800；上限 1200）
  → 每个 Parent 内按句号/问号/」 切 Child（目标 child_chars 150，范围 80–220）
  → 过短 Child 并入邻句；禁止半句切断（句边界优先于纯字数）
```

配置：

```yaml
novel_rag:
  narrative_hierarchy:
    enabled: true
    parent_chars: 800
    parent_overlap_chars: 80
    child_chars: 150
    index_parents: false      # Parent 不写向量
    expand_radius: 1          # 命中后左右各扩几个 Parent（同章）
    max_expanded_chars: 3500  # 单次举证总字数上限
    chapter_hard_boundary: true
```

### 3.4 检索时序

```text
query
  → IntentRouter → narrative（权重不变）
  → hybrid / vector search（仅 granularity=child 或仅有 vec 的行）
  → TopK Child（如 10）
  → expand:
       parent = get_block(child.parent_id)
       neighbors = ± expand_radius Parent（同 doc_id + 同章 + 序）
       去重 Parent → 拼原文；标注命中 Child 的 span（可选）
  → 可选：对 Parent 文本 rerank
  → _format_context：输出 Parent 原文 + block_id + 章节
```

兼容：

- 旧库无 `parent_id`：命中块即举证（现行为），`expand` no-op。  
- QA `ref_chunk_ids`：可继续指 Parent；新建 QA 优先挂 Parent id。  
- story-analysis：map 输入继续用 Parent（或 expand 后文本），证据 `block_id` 用 Parent。

### 3.5 实现落点

| 模块 | 改动 |
|------|------|
| `domain/novel/chunker.py` | `NovelChunker` 产 Parent+Child；或 `HierarchicalChunker` |
| `domain/novel/models.py` | `parent_id` / `granularity` / 邻接字段 |
| `infrastructure/lance_backend.py` | schema 增列或 metadata JSON；search 可过滤 child |
| `infrastructure/novel_store.py` | `search` 后可选返回 raw child；新增 `expand_narrative_hits` |
| `application/novel/retrieval.py` | 格式化前调用 expand；叙事通道默认开启 |
| `application/novel/ingest.py` | 索引时 Parent（无向量）+ Child（有向量） |
| `core/impersonation_agent.py` | `_retrieve_fact_context` 走同一 expand |
| `tests/` | 切块单测；expand 同章边界；旧块兼容 |

---

## 4. Dialogue：配额分层（够用即止）

### 4.1 目标

| ID | 目标 |
|----|------|
| D1 | 上传热路径成本与「优先角色够用样本」成正比，∝̸ 全书台词数 |
| D2 | 说话人判定保持 **LLM + 章/窗叙事上下文**（少正则） |
| D3 | 候选人与档位来自 Inventory；**知识库列表仍只展示 Inventory** |
| D4 | 建卡/扮演样本不足时可 L3 定向补抽 |

### 4.2 明确不做什么

- 默认不启用 `legacy_window` / 规则 Span 说话人主路径  
- 不用正则推断 `said_by`  
- 不因 Dialogue 发现的新名自动写入知识库候选主列表（可记 meta；展示以 Inventory 为准）

允许的轻量规则（非说话人引擎）：

- 章标题黑名单、无引号则跳过章（`require_quote_marks`）  
- `is_noise_speaker` 过滤入库  
- 用 Inventory 正名/别名做**章文本子串存在**，仅用于「下一窗抽哪一章」（定位，不归因）

### 4.3 四层结构

```text
L0  章过滤
      无引号 / 过短 / 简介·制作信息标题 → skip

L1  覆盖抽取（上传，有预算）
      有引号章 → 头/中/尾均匀交织选章
      每章默认 1 窗（超长章 ≤2）
      cloud_chapter 同款：窗文本（含叙事）→ LLM 抽台词+定说话人
      candidates = Inventory volume_seed（soft）
      受 max_calls_per_doc 约束

L2  配额验收 + 索引
      按 importance 累计每角色已收条数与章覆盖
      达标可提前 STOP 后续窗
      仅达标策略内的 turn 写入 dialogue 向量
      更新 Roster.dialogue_count（列表成员仍来自 Inventory）

L3  按需补抽（建卡 / 扮演 / 显式加强）
      仅目标角色相关章（别名子串命中章）再跑若干窗
      并入索引后再次 Gather
```

### 4.4 「够用」配额

| 档位 | 判定来源 | 每卷目标 turns（默认） | 章覆盖 |
|------|----------|------------------------|--------|
| main | Inventory `importance=main` | 100 | 有对话章的 ≥30% 各至少 1 条 |
| supporting | `supporting` 或 mention TopN | 50 | ≥ 4 章 |
| extra | `extra` / 未点名 | 10（上传可 0） | — |
| 未知/噪声 | speaker 无效 | **不索引**（`index_unknown: false`） | — |

整卷硬顶：

| 配置 | 默认 | 含义 |
|------|------|------|
| `mode` | `quota` | `quota` \| `full`（full≈现顺序抽满） |
| `max_calls_per_doc` | `60` | LLM 窗上限 |
| `max_turns_indexed_per_doc` | `1200` | 写入向量的 turn 上限 |
| `stop_when_priority_met` | `true` | main+supporting 达标可停 |

建卡侧对齐（已有设计）：Gather 默认 30～80 条；`min_dialogues` 默认 5。入库配额略高于 Gather，避免刚达标就 low_evidence。

### 4.5 L1 选窗算法（伪代码）

```text
seed, importance_map ← Inventory
chapters_with_quotes ← L0
order ← interleave(head, mid, tail) of chapters_with_quotes
calls ← 0
counts ← {name: 0}, coverage ← {name: set()}

for ch in order:
  if stop_when_priority_met and priority_satisfied(counts, coverage):
    break
  if calls >= max_calls: break
  windows ← plan_chapter_windows(ch)  # 1 or 2
  for w in windows:
    turns ← LLM.extract_window(w.text, candidates=seed)
    accept & quota-filter → buffer
    update counts/coverage/seed(new reliable names as extra)
    calls += 1
  if main_deficit(name):
    prioritize upcoming chapters where alias in chapter.text

index(buffer under max_turns_indexed)
```

### 4.6 与 Inventory / Roster 的边界

| 数据 | 权威来源 | Dialogue 可否改写 |
|------|----------|-------------------|
| 知识库候选列表、canonical、aliases | Inventory | **否**（只读 seed） |
| `dialogue_count` / 样本统计 | Dialogue 索引结果 | **是**（更新统计） |
| `has_card` / 人设正文 | 按需 Build | 否（本方案不改） |

### 4.7 L3 触发

| 触发 | 条件 | 行为 |
|------|------|------|
| `POST .../characters/build` | Gather `dialogue_hits < min_dialogues` 且 `deepen_on_build` | 对该角色补 `deepen_max_calls`（默认 8）窗 |
| 扮演首轮 | style 检索空/过少（可选） | 异步补抽，不阻塞首包可用 narrative |
| 手动 | API `dialogue/deepen` | 指定 `series_id` + character |

### 4.8 配置草案

```yaml
novel_rag:
  dialogue_attribution:
    enabled: true
    provider: cloud_chapter      # 保持；禁用作默认的 legacy_window
    mode: quota                  # quota | full
    max_chunk_chars: 6000
    slide_win_chars: 3500
    slide_stride_chars: 2000
    max_calls_per_doc: 60
    max_turns_indexed_per_doc: 1200
    stop_when_priority_met: true
    index_unknown: false
    accept_min: 0.5
    quotas:
      main: 100
      supporting: 50
      extra: 10
    min_chapter_coverage_main: 0.3
    supporting_top_n: 20         # mention 序补充 supporting 名额
    deepen_on_build: true
    deepen_max_calls: 8
    concurrency: 8
```

### 4.9 实现落点

| 模块 | 改动 |
|------|------|
| `application/novel/dialogue_pipeline.py` | `mode=quota` 选窗/停条件/验收；`full` 保留现逻辑 |
| `domain/novel/dialogue_chunk.py` | `interleave` 章序；按角色缺额重排 |
| `domain/novel/dialogue_llm.py` | 接口可不变 |
| `domain/novel/character_inventory.py` | 只读导出 `importance` map（若尚未暴露） |
| `application/novel/ingest.py` | 传入 importance；写 meta（配额达成情况） |
| `domain/novel/character_on_demand.py`（或 build 路径） | Gather 前/后 L3 deepen |
| `config.yaml` | 上表项 |
| `tests/requirements/`（F-03 配额 / F-05 噪声） | 配额停、均匀覆盖、未知不索引、deepen |

---

## 5. 运行时如何消费（④）

| 场景 | Narrative | Dialogue |
|------|-----------|----------|
| `novel_search` / 事实题 | Child 命中 → Parent 邻域原文 | 一般不主用 |
| 扮演口吻 | — | speaker 过滤 TopK（配额样本足够即可） |
| 扮演事实 | Child→Parent | — |
| 建卡 Gather | 叙事短段 | 对话 30～80；不足 L3 |
| 剧情分析 | Parent / 章级原文 | 可选共现，非必须 |
| 知识库列表 | — | 不决定列表；Inventory 决定 |

---

## 6. 迁移与兼容

### 6.1 已索引旧卷

| 情况 | 行为 |
|------|------|
| 无 `granularity` / `parent_id` | 视为「扁平 Parent=自身」；检索不 expand |
| 对话已全量抽过 | 不必重抽；新卷走 `mode=quota` |
| 需升级 narrative | 对该 `doc_id` 删块重 ingest，或提供 `reindex_narrative_hierarchy` 任务 |

### 6.2 默认开关

- 新上传：`narrative_hierarchy.enabled=true`，`dialogue mode=quota`  
- 一键回退：`narrative_hierarchy.enabled=false`（只产扁平块）；`dialogue mode=full`

### 6.3 观测 meta（上传结果增加）

```json
{
  "narrative": {"parents": 120, "children": 480, "index_parents": false},
  "dialogue": {
    "mode": "quota",
    "llm_calls": 42,
    "turns_indexed": 610,
    "stopped_reason": "priority_met|max_calls|exhausted",
    "per_character": {"利姆路": {"n": 100, "chapters": 12}}
  },
  "inventory": {"candidates": 35}
}
```

---

## 7. 分阶段落地

### Phase A — Dialogue 配额（优先，成本立减）

1. `mode=quota` + 交织选章 + importance 配额 + 提前停  
2. `index_unknown=false`  
3. 建卡路径 `deepen_on_build`  
4. 单测 + 一卷实跑对比：`llm_calls` / 主角色样本数 / 未知率  

### Phase B — Narrative 邻窗（低风险垫步，可选并行）

1. 仍用现 ~500 块；检索命中后 ±1 Parent 拼举证（档 A）  
2. 验证后记/事实题 faithfulness  

### Phase C — 真 Child→Parent

1. 切块产 Child/Parent；只索引 Child  
2. `retrieval` / impersonation / story-analysis 统一 expand  
3. Lance schema 迁移；旧书兼容  

### Phase D — 打磨

1. Parent 级 rerank、Child 章名前缀  
2. 配额按卷字数自适应  
3. 评估集：narrative Hit@K（金标段）+ dialogue 主角色覆盖 + 上传耗时/费用  

---

## 8. 验收标准

| 项 | 标准 |
|----|------|
| Inventory 列表 | 与改前一致（同文同配置下候选集合无回归需求） |
| Dialogue 主角色 | 每卷 main 达配额或 `stopped_reason` 可解释；扮演 TopK 非空 |
| Dialogue 成本 | 同卷 `llm_calls` 较 `mode=full` 明显下降（目标 ≥30%），主角色样本不崩 |
| 少正则 | 默认路径无 Span 说话人归因；无 `legacy_window` |
| Narrative 点查 | 金标段落 Hit@5 ≥ 现基线；举证含完整句段（非半句） |
| 兼容 | `hierarchy=false` / `mode=full` 可回退；旧块可读 |

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 配额导致配角口吻不足 | supporting 配额 + L3 deepen；extra 上传少抽 |
| 交织选章仍漏某主线章 | 缺额时按别名命中章优先补窗 |
| Child 过碎导致召回噪声 | child_chars 下限 + Parent rerank + 同章 expand |
| Lance 列迁移 | 新字段可空；扩展 JSON 兜底 |
| Inventory importance 不准 | 无 importance 时按 `mention_count` TopK 当 main/supporting |

---

## 10. 总结

| 层 | 策略 |
|----|------|
| 真源 | Parent narrative 原文 +（配额内）dialogue 块 |
| 检索 | narrative Child 命中 → Parent 邻域举证；dialogue 按 speaker |
| 角色列表 | **仅 Inventory** |
| 说话人 | **LLM + 章窗叙事**；Inventory 只供候选与档位 |
| 成本 | Dialogue 够用即停；Narrative Parent 默认可不向量 |

**一句话**：列表交给 Inventory 不动；原文用 Child 找、Parent 证；对话用带上下文的 LLM 按配额抽够就停，不够再按需补——全程少用正则认人。
