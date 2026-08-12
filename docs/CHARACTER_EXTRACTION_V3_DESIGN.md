# 角色名/关系抽取管线 V3 设计（glm 粗扫 → DeepSeek 精识别 → 规则清

> ✅ **2026-08-10 状态更新：已实施并验证**（史莱姆 vol01 翻译名场景）。
> 本文档描述已落地管线，实现以 `src/domain/novel/character_inventory/` 与 `character_policy.py` 为准。
洗）

> 日期：2026-08-09 | 状态：已实施并验证（史莱姆 vol01）
> 关联：`src/domain/novel/character_inventory/`、`src/shared/llm_config.py`、`config.yaml`、`scripts/dev/clean_inventory.py`

---

## 1. 三轮演进与动机

| 版本 | 方案 | 结果 | 放弃原因 |
|------|------|------|---------|
| v1 | LLM 全量盘点（一次扫 ≤12 万字符，head/mid/tail 采样） | 25 角色 | 35 万字只覆盖 34%，长尾角色丢失 |
| v2 | 按章分批 + type 标注 + evidence 归一（glm/DeepSeek 混跑） | 125 角色 | 泛称污染（商人/勇者/技能/物品混入 main/supporting）、每次运行漂移 |
| **v3（最终）** | **glm 粗扫（免费快）→ DeepSeek 带 evidence 精识别（1 次）→ 规则清洗** | **75 干净 + 47 关系** | — |

## 2. 最终管线（V3）

```
glm-4.7-flash 按章分批联合抽取（免费）
  └─ _SYSTEM_PROMPT：说话人 + 人物关系（带章节时间戳）
  └─ extract_names_by_chapter_batches：每批 ≤60k 字符（完整章），串行
      输出: {names: [{name, types}], relations: [{source, target, relation,
             evidence[], first_chapter, chapter_count}]}
            ↓
mentions_from_names（全文定位，防幻觉）
  └─ cluster_mentions（规则聚类：子串/编辑距离 ≤1 合并）
            ↓
DeepSeek-v4-pro 带 evidence 全局归一（1 次调用）
  └─ _SYSTEM_V2：以 evidence 为准裁决（保留/删除/合并/会说话的技能保留）
      输出: 干净角色表 + dropped（带 reason）
            ↓
规则清洗扫尾（clean_inventory.py，确定性）
  └─ DELETE_GENERIC（泛称/种族/地名/物品/作者）+ KEEP_SPEAKING_SKILLS（技能白名单）
            ↓
角色名单落盘 data/inventories/{series}.json
关系落盘   data/relations/{series}.json（跨卷合并，evidence + first_chapter + chapter_count）
```

## 3. 关键设计决策

### 3.1 为什么"glm 粗扫 + DeepSeek 精识别"（成本最优）

| 任务 | 频率 | 复杂度 | 模型 |
|------|------|--------|------|
| 实体/关系粗召回（分批，每批 1 次） | 高（7 批/卷） | 低 | glm（免费） |
| 全局归一/去噪裁决 | 低（1 次/卷） | 高（evidence 语义判断） | DeepSeek（强） |

**DeepSeek 调用从 8 次/卷（v2）降到 1 次/卷，成本 <¥0.2/卷**。

### 3.2 为什么说话人作为粗召回目标

**说话人 = 真实角色**（种族/称号/技能/地名不说话）→ 粗扫天然干净。对比"全实体提取"（商人/勇者/国家/蘑菇全进来）污染严重。

### 3.3 为什么归一必须带 evidence

实测：LLM 清洗**只看名字**（无上下文）→ 把作者"伏濑"、称号"爆焰支配者"当角色保留（失效）。
带 evidence（原文片段）→ 正确删除（"evidence 显示是作者署名/称号泛称"）。

### 3.4 史莱姆特性：会说话的技能

史莱姆的技能系统（大贤者/捕食者/魔法筒）有真实台词（脑内系统音《答。……》）→ 说话人提取会抓到它们。
归一规则：**会说话的技能保留（importance=extra），泛称删除**（"有明确名字/台词的技能 → 保留；无具体身份的泛称 → 删除"）。

### 3.5 关系抽取（联合输出，为关系图谱铺垫）

- 与实体同一 prompt 联合抽取（LightRAG/GraphRAG 同款）
- relation = 自由文本事实分析（"挚友/父子/敌对"），不限定枚举（现有 `classify_relation_type` 负责后续分类展示）
- 每条带 evidence（原文依据）+ first_chapter + chapter_count（时间戳，对齐 Graphiti 时序图谱）

## 4. 模型与调用点配置

`data/llm_config.json`（前端「设置」页可调）：

| 调用点 | 模型 | 用途 |
|--------|------|------|
| `character_inventory` | glm-4.7-flash | 分批粗扫（免费，并发=1 需串行） |
| `character_inventory_normalize` | deepseek-v4-pro | 全局归一（evidence 裁决） |
| `dialogue_extract` | 见 DIALOGUE_EXTRACTION_WITH_INVENTORY.md | 对话归因 |

`config.yaml → novel_rag.character_inventory`：
```yaml
ner: "llm"
llm_batch_chars: 60000    # 按章分批：每批 ≤此字符数（完整章，章边界零切半）
llm_max_names: 60
llm_max_tokens: 4096
```

## 5. 验证结果（史莱姆 vol01，29.2 万字）

| 项 | 结果 |
|----|------|
| 角色名单 | 75 个（main 5 全干净：凯金/哥布达/利姆露/皮兹/雷昂） |
| 关系 | 47 条（利姆露→维鲁多拉[挚友] ch1×2、利格鲁德→利格鲁[父子]、哥布达→大姐[单恋] 等） |
| 耗时 | 84s（glm 7 批 + DeepSeek 1 次）+ 规则清洗 |
| 成本 | <¥0.2/卷 |
| 单章验证 | 3 章全过（外传哥布达：2 保留 + 4 泛称删除） |

## 6. 已知限制与待办

| 项 | 说明 |
|----|------|
| 重复项合并 | 多尔德=维尔德拉、凯多=凯金、格兰·德瓦岗=盖札 未自动合并 |
| 规则词表维护 | DELETE_GENERIC / KEEP_SPEAKING_SKILLS 需按书扩充 |
| 全卷漂移 | glm 批次间输出有差异（免费模型极限）；DeepSeek 归一输入固定后稳定 |
| 关系接入图谱 | relations 数据已落盘，待接入 `relation_graph.py` + 前端 RelationshipGraph |

## 7. 文件索引

**核心实现**：
- `src/domain/novel/character_inventory/llm_ner.py`（_SYSTEM_PROMPT、extract_names_by_chapter_batches、_parse_names/_parse_relations）
- `src/domain/novel/character_inventory/builder.py`（build_character_inventory、_llm_normalize_global/_SYSTEM_V2）
- `src/domain/novel/character_inventory/candidates.py`（persist_relations）
- `src/application/novel/ingest/blocks.py`（双 client 接入 + 关系落盘）

**工具**：
- `scripts/dev/verify/inventory_slime_vol01.py`（全卷盘点验证）
- `scripts/dev/verify/validate_pipeline_chapter1.py`（单章管线验证）
- `scripts/dev/clean_inventory.py`（规则清洗）
- `scripts/dev/llm_clean_inventory.py`（LLM 清洗对比——弃用，仅留档）
