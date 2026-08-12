# 对话配额升档净化（别名冲突 + 黑名单 + 异写合并）

> 日期：2026-08-03 | 状态：**Implemented（P0/P1/P2）**  
> 相关：[DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md](./DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md) · [NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md](./NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md) · [CHARACTER_INVENTORY_DESIGN.md](./CHARACTER_INVENTORY_DESIGN.md)

## 1. 问题

`promote_importance_by_mentions` 接通后，配额机制可用（`main/target:50`），但 **main 人选被 mention 频次污染**：

| 现象（vol01） | 影响 |
|---------------|------|
| 「史莱姆」「哥布莉娜」占 main | 主线额度被物种/群体吃掉 |
| 「史莱姆」既是「利姆露」alias 又独立占档 | 对话归因到史莱姆时不计入利姆露配额 |
| 「维尔德拉」与「维鲁多拉」双档 | 异写分裂；假 main / n=0 占坑 |

## 2. 根因

```text
CLUENER → cluster → (LLM|fallback)
  → assign_importance_by_mentions(仅按 mention TopN)
  → build_quota_tracker 每人独立 target
```

1. **别名冲突**：A.name ∈ B.aliases，但仍建独立 canonical；弱宿主列出强异写时旧逻辑会跳过合并。  
2. **升档无语义**：不区分人物 / 物种；LLM importance 被覆盖。  
3. **`is_noise_speaker` 挡不住**「史莱姆」「哥布莉娜」。  
4. **`cluster_fallback`** 跳过 LLM「删物种」；本卷名单缺系列正名（利姆露）。  
5. **异写未合并**：编辑距离 2 的译名变体各占一档。

## 3. 规则

```text
inventory_characters ∪ series candidates
  → R1 merge_alias_collisions（并入 mention 更强方）
  → R1b merge_near_duplicates（len≥4 且编辑距离≤2）
  → R2 importance_blacklist
  → R3 assign_importance_by_mentions
  → QuotaTracker targets
```

| ID | 规则 |
|----|------|
| R1 | 双向 alias 关联 → 保留 mention 更高（并列更长名），另一方变 alias |
| R1b | 两名均 ≥`near_duplicate_min_len`、首字相同、共享字数足够、且 Levenshtein ≤`near_duplicate_max_distance` → 同上合并 |
| R2 | 黑名单 exact → 不进 main/supporting TopN |
| R3 | 仅在合格池内按 mention TopN 分档 |

## 4. 配置

```yaml
merge_alias_collisions: true
merge_near_duplicates: true
near_duplicate_max_distance: 2
near_duplicate_min_len: 4
importance_blacklist: [史莱姆, 哥布林, 哥布莉娜, ...]
```

## 5. 实现映射

| 层 | 模块 | 行为 |
|----|------|------|
| P0 | `dialogue_quota.py` | R1/R2/R3、`diagnostics` |
| P0 | `dialogue_pipeline.py` | 传参 + meta；`_attr_config` → 仓库根 `config.yaml` |
| P1 | `character_inventory` + `ingest` | 系列 candidates 注入/合并 |
| P2 | `merge_near_duplicates` + R1 强宿主修正 + `resolve` 编辑距离≤2 | 维尔德拉↔维鲁多拉 |

## 6. 验收（vol01）

- main 不含黑名单名；利姆露 `main` 且 `n>0`  
- `merged_alias_collisions` / `merged_near_duplicates` 可见  
- 维鲁多拉 / 维尔德拉 不同时各占独立 main；`resolve` 互通  

## 7. 非本轮

- 两阶段 dialogue_count 重分档  
- 改 CLUENER / 子串聚类算法本身  
