# 按需单角色生成：详细设计

> 状态：In Progress (D0/D1/D4 UI landed) | 同步 2026-07-28  
> 前置理念：索引为真源（历史总案见 `archive/NOVEL_INDEX_FIRST_DESIGN.md`）  
> 相关：`DIALOGUE_CHAPTER_EXTRACT_DESIGN.md`、`CHARACTER_INVENTORY_DESIGN.md`、`ADR-004`；短窗归因归档见 `archive/DIALOGUE_SPEAKER_ATTRIBUTION_DESIGN.md`  
> **已落地**：Roster L1、AliasMap、上传默认 `generate_character_llm=false`、`POST /api/v1/agent/characters/build`（`wait=false` 异步）、Knowledge 勾选生成 UI

---

## 1. 一句话

**系统出角色候选表 → 用户只勾选要生成谁 → 系统自动完成正名/别名合并、库内抽取、人设蒸馏、写卡。**  
上传默认不批跑全角色 LLM；联网只服务「正名与别名」，不写人设正文。

---

## 2. 目标与非目标

### 2.1 目标

| ID | 目标 |
|----|------|
| C1 | 用户交互仅「选系列/卷 + 勾选角色（可模糊搜）」 |
| C2 | 正名、别名统一、消歧、证据检索、蒸馏、落库全自动 |
| C3 | 人设真源 = 本系列已索引的 narrative / dialogue，外网不得覆盖原文 |
| C4 | LLM 成本 ∝「用户点过的角色」，∝̸「全书角色数 × 卷数」 |
| C5 | 与 ImpersonationAgent / 四通道 / Roster L1 兼容 |

### 2.2 非目标（本期）

- 上传后默认「生成本书全部人设」
- 让用户手填别名表 / 审 Wiki 全文
- 用外网百科正文当性格与剧情来源
- 完美跨作品同名消歧（多系列同装时仅保证 `series_id` 隔离）

---

## 3. 职责分工

| 角色 | 负责 | 不负责 |
|------|------|--------|
| **用户** | 选书/系列；勾选要生成的角色；可选点「强制重生」 | 填全名、维护别名、确认外网条目（常态） |
| **系统 · 发现** | 入库后写 L1 Roster（频次/称呼/共现） | 调云端为人设 |
| **系统 · 规范** | `canonical_name` + `aliases[]`；可选联网对齐公开 IP | 把外网剧情写进卡 |
| **系统 · 抽取** | 按规范名+别名捞对话/叙事证据包 | — |
| **系统 · 蒸馏** | 单角色 LLM → CharacterCard / character 块 | 全角色 unified 批抽（默认关） |

**例外 UI**：仅当自动消歧置信度低于阈值（同系列出现两个高冲突候选）时，弹一次「你指的是 A 还是 B？」——非常态。

---

## 4. 端到端流程

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────────────────────┐
│ 上传/索引 L0 │ ──► │ 写 Roster L1 │ ──► │ Knowledge：展示候选列表      │
│ 无角色 LLM  │     │ 无云端 LLM   │     │ （次数 / 是否已有卡）         │
└─────────────┘     └──────────────┘     └──────────────┬──────────────┘
                                                        │ 用户勾选 1..N
                                                        ▼
                                         ┌──────────────────────────────┐
                                         │ CharacterBuildJob（每角色一单）│
                                         └──────────────┬───────────────┘
                                                        │
          ┌───────────────┬───────────────┬─────────────┴────────────┐
          ▼               ▼               ▼                          ▼
     Normalize       GatherEvidence    DistillLLM              Persist
     正名+别名        库内检索包         单角色档案               Card+可选块
     (可含 web)       (无则 low_ev)      (1~少数次)               更新 Roster
```

批处理形态：用户勾选 5 人 = **串行或有限并发的 5 个单角色 Job**，不是「一次 prompt 输出全书」。

---

## 5. 数据模型

### 5.1 标识

| 字段 | 说明 | 例 |
|------|------|----|
| `series_id` | 系列 | `tensura` |
| `doc_id` | 卷（可选范围） | `tensura__vol01` |
| `character_id` | 稳定 ID | `{series_id}__{canonical_slug}` |
| `canonical_name` | 展示与检索主名 | `利姆路` |
| `aliases` | 并入同一实体的称呼 | `["利姆鲁","头目","史莱姆"]` |

路径：`data/characters/{series_id}__{canonical_slug}.json`（打破仅按人名撞库）。

### 5.2 Roster 条目（L1，上传后即有）

```json
{
  "name": "利姆路",
  "aliases_observed": ["头目", "利姆路大人"],
  "dialogue_count": 128,
  "mention_count": 340,
  "chapters": ["序章", "第一章"],
  "co_occurrence": {"利格鲁": 12, "兰加": 40},
  "status": "candidate",
  "character_id": null,
  "has_card": false
}
```

`status`：`candidate` | `building` | `ready` | `low_evidence` | `failed`。

### 5.3 AliasMap（系列级，系统维护）

路径建议：`data/rosters/{series_id}.alias.json` 或嵌入 series roster。

```json
{
  "series_id": "tensura",
  "entities": [
    {
      "character_id": "tensura__rimuru",
      "canonical_name": "利姆路",
      "aliases": ["利姆鲁", "头目", "利姆路大人", "史莱姆"],
      "source": {
        "corpus": ["头目", "利姆路大人"],
        "web": ["Rimuru Tempest", "利姆鲁"],
        "merged_at": "2026-07-26T12:00:00Z"
      },
      "confidence": 0.92
    }
  ]
}
```

**合并规则（系统）**

1. 以 Roster 高频名为种子。  
2. 本库规则：`X大人` / `叫道「X」` / 对话前后称呼 → 候选 alias。  
3. 可选 Web：`"{书名} {候选名} 角色"` → 解析正名与别名列表。  
4. Union-Find / 传递闭包合并到同一 `character_id`。  
5. `canonical_name` 选取：Web 高置信正名 > 本库对话 speaker 最高频规范形 > 用户勾选时的展示名。

### 5.4 CharacterBuildJob

```json
{
  "job_id": "cb_...",
  "series_id": "tensura",
  "doc_id": null,
  "input_name": "头目",
  "character_id": "tensura__rimuru",
  "canonical_name": "利姆路",
  "aliases": ["头目", "利姆鲁", "..."],
  "state": "normalize|gather|distill|persist|done|failed",
  "evidence": {
    "dialogue_hits": 42,
    "narrative_hits": 15,
    "sample_ids": ["..."]
  },
  "flags": {"used_web": true, "low_evidence": false},
  "error": null,
  "card_path": "data/characters/tensura__rimuru.json"
}
```

### 5.5 CharacterCard（L3，增量字段）

在现有卡上增加：

| 字段 | 说明 |
|------|------|
| `series_id` / `character_id` | 作用域 |
| `canonical_name` / `aliases` | 与 AliasMap 一致 |
| `source_doc_ids` | 蒸馏采样来源卷 |
| `evidence_hash` | 证据包指纹；索引变更可 stale |
| `prompt_version` | 如 `persona_v3` |
| `built_at` / `stale` | 失效标记 |

人设正文（personality 等）**只允许**来自本库证据 + 蒸馏模型，禁止直接粘贴网页。

---

## 6. 流水线详设计

### 6.1 Normalize（系统）

**输入**：`series_id`、用户勾选的 `input_name`（来自列表或搜索框）、可选 `doc_id`。

**步骤**

```text
1. 在 AliasMap / Roster 中解析 input_name
   - 已是某实体 alias → 直接取 character_id
   - 未收录 → 新建临时实体，canonical=input_name
2. 本库扩展 aliases
   - speaker 变体、称呼、共现称呼簇
3. if series.web_normalize_enabled and looks_like_public_ip:
     web_search(书名 + 角色)
     → 抽取候选正名/别名（结构化，限短字段）
     → 与本库簇合并；冲突时本库对话频次优先，Web 仅补外文名/常见异写
4. 消歧
   - 若 input 命中 ≥2 个高分实体且分差 < δ → 返回 need_disambiguate（唯一需用户点一下的情况）
   - 否则自动选定
5. 写出/更新 AliasMap；返回 {character_id, canonical_name, aliases[]}
```

**Web 约束**

| 允许写入 | 禁止写入 |
|----------|----------|
| canonical 候选、aliases、外文名 | personality / background 长文 |
| `source.web` 元数据 | 未出现在本书的剧透关系当事实 |

`WEB_NORMALIZE=0` 时跳过第 3 步，纯本库也能闭环。

### 6.2 GatherEvidence（系统）

**检索查询集合**：`canonical_name ∪ aliases`（过短别名如「我」排除）。

| 通道 | 过滤 | 用途 |
|------|------|------|
| dialogue | `series_id` + speaker ∈ 名集合（及模糊） | 口吻、口头禅 |
| narrative | `series_id` + 文本含名集合 | 身份、关系锚点 |
| character | 已有旧块 | 可选对照，不覆盖 |

**证据包**（送入蒸馏，有上限）：

```text
dialogues: top_n by score / 章节覆盖（默认 30～80 条）
narratives: top_m 短段（默认 10～20）
meta: dialogue_count, chapter_span, co_occurrence top
```

**证据阈值**

| 条件 | 行为 |
|------|------|
| dialogue_hits ≥ `min_dialogues`（默认 5） | 正常蒸馏 |
| 0 < hits < min | `low_evidence=true`，仍蒸馏但卡上打标；UI 提示「样本偏少」 |
| hits = 0 | Job `failed` 或 `low_evidence` 且不写高质量卡；提示检查 speaker 归因 / 别名 |

说话人质量是硬依赖：见现行 `DIALOGUE_CHAPTER_EXTRACT_DESIGN.md`（默认 `cloud_chapter`）。别名未并入会导致「利姆路」卡空、「头目」另册。

### 6.3 DistillLLM（系统 · 单角色）

**替换**默认路径上的 `extract_characters_unified`（全角色一大 JSON）。

**Prompt 原则**

- 只分析**这一个** `canonical_name`（及 aliases 说明「下文中的 X/Y 均指同一人」）。  
- 字段与现有 PersonalityProfile / CharacterCard 对齐（traits 六维、speech、catchphrases 等硬编码名）。  
- 要求：结论必须能被证据支持；不知则写「文中未体现」。  
- 温度偏低（事实感）；禁止引入证据外设定。

**调用预算**：每个 Job **1 次主调用**；JSON 解析失败可 **1 次修复调用**。禁止按章节循环打满。

### 6.4 Persist

1. 写 `CharacterCard` JSON（`series_id__slug`）。  
2. 可选：upsert 一条 `block_type=character`（便于 character 通道）。  
3. 更新 Roster：`has_card=true`、`character_id`、`status=ready|low_evidence`。  
4. 记录 `evidence_hash`；该系列新卷入库时可将相关卡标 `stale`（下次生成或扮演时重建）。

### 6.5 与扮演路径关系

| 模式 | 行为 |
|------|------|
| 已有 ready 卡 | Impersonation 可用卡 + 每轮仍检索 dialogue 校准 |
| 无卡 / 用户未点生成 | 允许 L2 纯检索组装扮演（index-first）；不强制先 Build |
| stale 卡 | 组装优先或后台静默 rebuild |

产品可选：**Knowledge 页「生成」** 与 **扮演页「首次自动 enqueue Build」** 并存；后者仍是单角色 Job，不是全表批跑。

---

## 7. API

### 7.1 列表（系统发现，用户浏览）

`GET /api/v1/agent/characters?series_id=&doc_id=`

```json
{
  "series_id": "tensura",
  "items": [
    {
      "name": "利姆路",
      "aliases_observed": ["头目"],
      "dialogue_count": 128,
      "has_card": true,
      "status": "ready",
      "character_id": "tensura__rimuru"
    }
  ]
}
```

排序：`dialogue_count` desc；可 `q=` 过滤。

### 7.2 触发生成（用户只传「选谁」）

`POST /api/v1/agent/characters/build`

```json
{
  "series_id": "tensura",
  "doc_id": null,
  "names": ["利姆路", "兰加"],
  "force": false,
  "web_normalize": null
}
```

- `names`：勾选结果；可以是别名，系统 Normalize。  
- `web_normalize`：`null` = 跟全局配置；`true/false` 覆盖。  
- 响应：`{ "jobs": [ { "job_id", "input_name", "state" } ] }`

`GET /api/v1/agent/characters/jobs/{job_id}` → 进度与结果摘要。  
`GET /api/v1/agent/characters/jobs?series_id=` → 最近任务。

### 7.3 消歧（例外）

若 Normalize 返回冲突：

```json
{
  "need_disambiguate": true,
  "input_name": "勇者",
  "candidates": [
    {"character_id": "...", "canonical_name": "希兹欧", "score": 0.55},
    {"character_id": "...", "canonical_name": "其他勇者", "score": 0.52}
  ]
}
```

`POST .../build` 可带 `resolve: { "input_name": "勇者", "character_id": "..." }` 继续。

### 7.4 兼容

- `POST .../characters/{name}/distill` → 内部转单角色 Job（name URL 解码后当 `input_name`）。  
- `PUT .../characters/{name}` → 人工编辑 escape hatch（可选锁 `locked: true` 防自动覆盖）。  
- 默认 **不提供**「一键全书角色」；若运维需要，仅 `POST .../build/batch_top_k?k=20` 显式、限流。

---

## 8. 前端（Knowledge）

1. 选系列 / 卷。  
2. 表格：角色名、别名预览、对话数、卡片状态、勾选框。  
3. 主按钮：**生成选中角色**（禁用条件：未选）。  
4. Job 进度：逐行 status（规范中 / 抽取中 / 完成 / 样本不足）。  
5. 不展示「请填写全名」「请确认 Wiki」常态表单。  
6. `low_evidence`：黄色提示，仍可打开卡预览。

扮演页：选「系列 + 角色」；若 `has_card=false`，可提示「将按原文即席扮演，或去知识库生成人设卡」。

---

## 9. 配置

```yaml
character_on_demand:
  enabled: true
  min_dialogues: 5
  max_dialogue_samples: 60
  max_narrative_samples: 20
  distill_max_retries: 1
  job_concurrency: 2
  web_normalize:
    enabled: true          # 公开 IP；原创书可系列级关闭
    allow_demo: false
  disambiguate_margin: 0.08
  card_stale_on_new_volume: true
```

环境变量建议：`CHAR_WEB_NORMALIZE=1`、`CHAR_BUILD_CONCURRENCY=2`。

---

## 10. 与现有代码的映射

| 现有 | 变迁 |
|------|------|
| `ingest` 内 `extract_characters_unified` | 默认关闭；不进上传热路径 |
| `CharacterCard.build(name, store)` | 扩展为 `build(input_name, store, series_id=..., aliases=...)`；先 Normalize+Gather 再蒸馏 |
| `character_builder` / PersonalityProfile | Distill 输出适配器复用字段 |
| `ImpersonationAgent` 无卡报错 | 改为可 L2 启动；有卡则用卡 |
| Roster | 上传必写；Build 回写 status |
| `web_search` 工具 | Normalize 内部可调同一搜索实现；结果不进扮演工具事实除非用户另搜 |

---

## 11. 失败与边界

| 场景 | 处理 |
|------|------|
| 外网空/错 | 仅本库 aliases；Job 不失败 |
| 外网与本书冲突 | 本库证据优先；Web 异写可进 aliases，正名以本库高频为准 |
| speaker 大面积错误 | Gather 空或脏 → low_evidence；优先修归因，而非加 Web |
| 用户勾选路人甲 | 允许；low_evidence 提示 |
| 同系列重名（少见） | 消歧 UI |
| force=true | 忽略旧卡，重跑 Gather+Distill；AliasMap 可保留 |

---

## 12. 观测与验收

| 指标 | 目标 |
|------|------|
| `character_build_llm_calls` | ≈ 1～2 × 成功 Job 数 |
| `normalize_web_calls` | ≤ 1 × Job（可缓存按 series+name） |
| `build_success_rate` | 主角色（对话≥min）≥ 95% |
| `alias_coverage` | 人工抽检：勾选「头目」落到「利姆路」卡 |
| `no_web_in_personality` | 抽检卡正文不含百科套话/未入库剧情 |

验收用例：

1. 仅索引 vol01 → 勾选「利姆路」→ ready 卡，含对话样本。  
2. 勾选「头目」→ 同一 `character_id`，不新建第二张卡。  
3. `CHAR_WEB_NORMALIZE=0` → 仍能生成。  
4. 上传不产生 N 次角色 LLM。  
5. 勾选 3 人 → 3 个 Job，非 1 次 unified 全表。

---

## 13. 实施阶段

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **D0** | Roster L1 + 列表 API；上传关 unified | index-first P0 |
| **D1** | 单角色 Gather+Distill+写卡；`POST /build`；无 Web | CharacterCard 路径含 series |
| **D2** | 本库 AliasMap 合并；「头目→利姆路」 | 称呼/speaker 规则 |
| **D3** | Web normalize（可开关）+ 消歧例外 UI | web_search 稳定 |
| **D4** | Job 队列/并发、stale、前端勾选生成 | — |

建议：**D0→D1 即可产品可用**；D2 是正确性关键；D3 锦上添花。

---

## 14. 决策摘要

| 决策 | 选择 |
|------|------|
| 谁决定生成谁 | 用户勾选 |
| 谁做全名/别名 | **系统**（本库为主，Web 可选增强） |
| 人设从哪来 | **仅本库检索证据 + 单角色蒸馏** |
| 批生成 | 默认不做；显式 Top-K 运维口另开 |
| 用户确认 | 仅自动消歧失败时 |

**产品句式**：选好角色，点生成——正名、别名、抽取、建卡，系统一次做完。
