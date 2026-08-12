# CLUENER + LLM 角色盘点与实体归一

> ⚠️ **2026-08-10 状态更新：主路径已废弃**。
> 粗召回源已切换为 `character_inventory.ner: "llm"`（一次 LLM 扫全文替代 CLUENER 本地模型，
> A/B 实测零噪声/长名完整/快 10 倍）。CLUENER 仅作降级可选。本文档描述历史主路径，
> 具体实现以 `config.yaml → novel_rag.character_inventory` 与 `src/domain/novel/character_inventory/` 为准。


> 状态：Implemented (P0/P1) | 同步 2026-08-05  
> 相关：`MODULE_SPLIT_AND_SEED_HYBRID_WORKLOG.md`、`DIALOGUE_CHAPTER_EXTRACT_DESIGN.md`、`analysis/` NER 评测报告；短窗归因归档见 `archive/DIALOGUE_SPEAKER_ATTRIBUTION_DESIGN.md`

## 流程

```text
原文 → CLUENER 人名召回 → 子串聚类 → LLM 补全名/删噪声
     → Roster L1 + AliasMap
     → 作为对话归因 volume_seed / candidates
```

## 模块

| 模块 | 路径 |
|------|------|
| NER + 聚类 | `src/domain/novel/character_ner.py` |
| LLM 归一 + 落库 | `src/domain/novel/character_inventory/` |
| Ingest 接线 | `src/application/novel/ingest/`（归因前跑 Inventory） |

## 配置

```yaml
novel_rag:
  character_inventory:
    enabled: true
    device: cpu
    max_chars: 80000
    llm_batch_size: 30
    merge_decay: 0.85        # 跨卷合并旧 mention 衰减（1.0=不衰减）
    seed:
      mode: hybrid          # fixed | percentile | hybrid
      min_mentions: 2
      percentile: 70
      top_k: 30
      small_n_fallback: 8
      small_n_top: 8
      blacklist_from_quota: true
      min_degree: 0         # 孤立噪声剪枝；需 data/graphs/ 关系图落盘后开启
```

LLM seed 流水线：黑名单 → `min_mentions` → 百分位门槛 → `top_k`；角色数过少时改 Top-N。
全量 candidates 仍落盘；`seed_names` / `in_llm_seed` 仅供对话归因先验。

**跨卷合并衰减**：`persist_inventory_candidates` 合并旧卷时，旧记录 `mention_count` 按
`merge_decay`（默认 0.85）衰减，再与本卷新值取 `max`。本卷未出现的旧角色同样衰减，
避免早期卷高频角色永久绑架 percentile 阈值（新卷黑马进不了 seed）。

**min_degree 剪枝**：`build_llm_seed` 支持 `degree_map`（角色 → 关系图 degree）。
`seed.min_degree > 0` 且有落盘关系图（`data/graphs/`）时，degree < min_degree 的孤立
噪声在阈值计算前剔除；无图数据时保持惰性（不误杀）。`load_series_degree_map(series_id)`
聚合系列各卷角色最大 degree。

环境变量：`NOVEL_CHAR_INVENTORY_ENABLED=true|false`；
`NOVEL_CHAR_SEED_THRESHOLD_MODE` / `NOVEL_CHAR_SEED_MIN_MENTIONS` 可覆盖 seed 策略。

## 降级

- 无 LLM：仅聚类草稿写入（`llm_skipped`）
- NER 失败：回退对话 speaker 名录
