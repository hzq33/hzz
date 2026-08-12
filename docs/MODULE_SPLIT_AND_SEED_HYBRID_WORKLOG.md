# 模块拆分修复、清理与 Seed Hybrid 工作纪要

> 同步：2026-08-05  
> 范围：拆分回归修复 → 死代码清理 → 测试套件重写 → LLM seed / 归因候选生产化改造

## 1. 背景

大文件拆成包后日常路径可用，但存在拆分引入的静默回归（路径深度、`_inventory_config` 未导出、`IngestAbortError`/`IngestResult` NameError）。随后清理失效生成脚本、重写单元测试，并按生产常见做法改造「全书 seed + 章内 prompt 候选」策略。

## 2. 拆分回归修复（已完成）

| 问题 | 修复 |
|------|------|
| `ingest/blocks.py` 宽 `except` 吞掉 `IngestAbortError`，再返回未定义 `IngestResult` → NameError | 重抛 `IngestAbortError`；其它错误包装为 `IngestAbortError` |
| `character_inventory` 未再导出 `_inventory_config`，ingest 库存被静默跳过 | `__init__.py` 导出 `_inventory_config` |
| 拆分包 `Path(__file__).parents[N]` 仍按单体深度 → 读不到根 `config.yaml`，数据写到 `src/data/` | 统一改为指向仓库根（包内子模块一般为 `parents[4]`） |
| `ingest` 包未再导出脚本用的内部符号 | `__init__.py` 补回 `_convert_to_md` 等；`_detect_chapters_via_llm` 兼容包装 |
| 开发脚本把元组返回值当字符串 | `scripts/dev/*` 改为 `[0]` 解包 |

涉及包：`ingest/`、`dialogue_pipeline/`、`character_inventory/`、`dialogue_quota/`、`story_analysis/`、`character_on_demand/`。

## 3. 死代码与结构整理（已完成）

**删除**（读已删单体、无法再跑）：

- `scripts/dev/split_{dialogue_pipeline,ingest,character_*,name_resolver,dialogue_quota,story_analysis}.py`
- `extract_phase3.py`、`gen_blocks.py`、`assemble_character_builder.py`

**迁移**到 [`scripts/dev/refactor/`](../scripts/dev/refactor/README.md)（源文件仍在、可偶尔重跑）：

- `split_impersonation.py` → `_imp_*.py`
- `split_llm.py` → `_llm_resilience.py`
- `split_builtin_novel.py` → `_novel_search_handlers.py`

**其它**：去掉 `ingest/__init__.py` 无用 `import re`；删除过时 `ruff.toml` 对已删 `ingest.py` 的例外；更新 [`ARCHITECTURE.md`](./ARCHITECTURE.md) / [`CHARACTER_INVENTORY_DESIGN.md`](./CHARACTER_INVENTORY_DESIGN.md) 包路径。

## 4. 测试套件重写（已完成）

旧 `tests/test_*.py` 已删除且**未从 git 恢复**。新套件离线、无 LLM/网络，覆盖拆分包与回归点：

| 文件 | 覆盖 |
|------|------|
| `tests/conftest.py` | 临时 SQLite / inventory / story analysis 目录 |
| `test_novel_ingest_helpers.py` | doc_id、上传校验、`IngestAbortError` |
| `test_character_inventory_candidates.py` | 候选持久化、卷/系列合并 |
| `test_llm_seed_hybrid.py` | hybrid seed、黑名单、top_k、prompt 候选 |
| `test_dialogue_quota.py` | 配额 / 窗口 / turn 过滤 |
| `test_dialogue_pipeline_helpers.py` | provider / turns→blocks |
| `test_name_resolver.py` | 别名聚类 |
| `test_story_analysis_helpers.py` | 路径与 snapshot |
| `test_character_job_store.py` | 角色任务 SQLite |
| `test_split_exports_and_paths.py` | 导出与 `parents[4]` 回归 |

`pytest.ini`：去掉已删 E2E ignore，增加 `integration` marker。

运行：`python -m pytest tests -q`

## 5. LLM Seed Hybrid + 章内候选（已实现）

### 5.1 问题

旧策略仅用 **mention 中位数** 筛 seed：对龙套密集书偏松；高频物种名（史莱姆等）进 prompt；无硬 Top-K；归因时整表 `volume_seed` 灌进每窗 LLM。

### 5.2 设计（对齐 GraphRAG / EL / 文学归因常见做法）

三层数据职责：

```text
① 全量 candidates     → 落盘 / 配额 TopN（不过度裁剪）
② 全书 volume_seed    → LLM 先验（严：黑名单+下限+百分位+top_k）
③ 章/窗 prompt cands  → 真正塞进当次 prompt（局部优先 + 硬上限）
```

### 5.3 实现要点

**Seed 算法** — [`src/domain/novel/character_inventory/candidates.py`](../src/domain/novel/character_inventory/candidates.py)

- 新入口：`build_llm_seed()` → `SeedBuildResult`
- 流水线：黑名单（复用 `importance_blacklist`）→ `min_mentions` → percentile（hybrid 默认 P70）→ `top_k`
- 角色数 `< small_n_fallback`：不跑百分位，改 Top-N
- `filter_seed_characters` / `seed_names_from_inventory` / `persist_inventory_candidates` 改为走 `build_llm_seed`
- 兼容：旧 `median` → percentile@50；环境变量仍可用

**章内候选** — [`dialogue_pipeline/tools.py`](../src/application/novel/dialogue_pipeline/tools.py) `assemble_prompt_candidates`

- 局部文本/高置信 span → `volume_seed ∩ 本章文本` → 冷启动用 volume_seed 补齐
- [`extract.py`](../src/application/novel/dialogue_pipeline/extract.py) 每窗 LLM 调用改用组装结果，不再 `cands = list(seed)`
- sanitize / 配额仍可用较全的 `seed` 表

### 5.4 配置

```yaml
novel_rag:
  character_inventory:
    seed:
      mode: "hybrid"       # fixed | percentile | hybrid
      min_mentions: 2
      percentile: 70
      top_k: 30
      small_n_fallback: 8
      small_n_top: 8
      blacklist_from_quota: true
  dialogue_attribution:
    max_prompt_candidates: 10
    prefer_local_over_volume_seed: true
```

详见 [`CHARACTER_INVENTORY_DESIGN.md`](./CHARACTER_INVENTORY_DESIGN.md)。

## 6. 关键文件一览

| 区域 | 路径 |
|------|------|
| Seed | `src/domain/novel/character_inventory/candidates.py` |
| 归因候选 | `src/application/novel/dialogue_pipeline/{tools,extract,config}.py` |
| 配置 | `config.yaml` |
| 测试 | `tests/test_*.py`、`tests/conftest.py`、`pytest.ini` |
| 重构脚本 | `scripts/dev/refactor/` |
| 文档 | 本文、`CHARACTER_INVENTORY_DESIGN.md`、`ARCHITECTURE.md` |

## 7. 建议后续

**已实施（2026-08-05，见 §9）：**

1. ✅ 用 1～2 本真实书对比旧中位数 vs hybrid：主角是否保留、物种是否被挡、`seed_size` / `prompt_cand` 分布 → `docs/analysis/SEED_MEDIAN_VS_HYBRID_2026-08-05.md`
2. ✅ series 合并后的 mention 衰减（避免旧卷绑架阈值）→ `merge_decay` 配置
3. ✅ `min_degree` 剪孤立噪声（已实现；需 `data/graphs/` 关系图稳定落盘后开启）

**仍待前置（未做）：**

4. 不建议立刻上 LLM salience 二刷（成本高、与 normalize 重叠）

> 注：第 3 项实现为**惰性开关**——`seed.min_degree: 0` 默认关闭；当前 `data/graphs/`
> 目录为空（真实 ingest 尚未跑到 graph 构建阶段），故暂不生效。等关系图稳定产出后
> 把 `min_degree` 调为 1 即可，无需改代码。

## 8. 验证命令

```bash
python -m pytest tests -q
# 重点：
python -m pytest tests/test_llm_seed_hybrid.py tests/test_split_exports_and_paths.py tests/test_novel_ingest_helpers.py -q
```

## 9. 后续实施纪要（2026-08-05）

### 9.1 真实书对比：legacy median vs hybrid

脚本 `scripts/dev/compare_seed_median_vs_hybrid.py`（真实 inventory JSON + 真实卷 7 章节文本，离线无 LLM）：

| 书 | candidates | legacy median | hybrid | 物种进 seed |
|----|-----------|---------------|--------|------------|
| 关于我转生变成史莱姆这档事（6 卷） | 128 | thr=5, seed=67 | thr=11, seed=30 | 哥布莉娜/史莱姆 → ∅ |
| 维鲁多拉的史莱姆成史观察日记（15 卷） | 89 | thr=3, seed=48 | thr=6, seed=29 | 哥布莉娜/史莱姆 → ∅ |

- 主角（importance=main）5 人两策略全保留
- hybrid 下 `seed_size` 收敛到 `top_k=30`；旧 median 无硬 Top-K（67/48）
- 物种名旧策略混入 seed（史莱姆 42 mentions 且 `in_llm_seed=True`），hybrid 全挡
- prompt_cand（真实卷 7 章节，prefer_local）：两策略 avg≈10.0、local_hit=100，
  seed_hit 1→2——volume_seed 主要承担冷启动兜底，章内局部候选优先的预期成立

### 9.2 series 合并 mention 衰减

- 配置 `novel_rag.character_inventory.merge_decay`（默认 `0.85`，`1.0`=旧行为）
- `persist_inventory_candidates` 合并旧卷：旧 `mention_count × decay` 再与当卷值 `max`；
  当卷未出现的旧角色同样衰减
- meta 记录 `merge_decay` / `merged_docs` / `degree_dropped`
- 真实数据回放（15 卷书，模拟后续卷合并）：P70 阈值 6 → 4 → 3（2/4/6 轮），
  旧卷高频角色不再永久绑架阈值；`seed_size` 保持 top_k=30
- 新增测试：`test_merge_decays_stale_volume_characters`、`test_merge_decay_one_disables_decay`、
  `test_merge_decay_stops_threshold_hijack`

### 9.3 min_degree 剪孤立噪声

- `seed.min_degree`（默认 `0` 关闭）；`build_llm_seed(..., degree_map=...)` 在阈值前剪
  degree < min_degree 的节点；无 degree_map 时惰性（不误杀）
- `load_series_degree_map(series_id)` 聚合 `data/graphs/<series>__*.json` 各卷角色最大
  degree（兼容 networkx MultiGraph 的 `edges` 与普通 `links` 格式）
- `persist_inventory_candidates(..., degree_map=...)` 透传；配置开启时自动加载 series 图
- 诊断：`data/graphs/` 为空系真实 ingest 未到 graph 阶段（`build_graph` 需
  character+dialogue blocks 非空，dialogue 抽取依赖 LLM），非代码缺陷；smoke 脚本
  `scripts/dev/smoke_character_graph.py` 验证 build/save/load 正常
- 新增测试：min_degree 剪枝 / 无图惰性 / 缺图不误杀 / series degree 聚合 / 空图返回 {}

### 9.4 启动回归修复：`_imp_models` 漏改名（2026-08-05 晚）

- 症状：`python -m uvicorn agent_server:app` 启动即崩，
  `ModuleNotFoundError: No module named 'src.core._imp_models'`
- 根因：commit `267a269` 把 `src/core/_imp_{chat,models,retrieval}.py` 改名为
  `src/core/impersonation/{chat,models,retrieval}.py` 并"同步 4 处 import"，但
  **`impersonation/chat.py` 与 `impersonation/retrieval.py` 内部仍引用旧名**
  `from src.core._imp_models import ...`，导致 `impersonation_agent → impersonation.chat`
  导入链断裂。此 bug 藏在拆分收尾 commit 中，单元测试未覆盖启动导入链。
- 修复：两处 import 改为 `from src.core.impersonation.models import ...`
- 验证：导入链 smoke（`create_impersonation_agent` / mixins / `import agent_server` 全通）；
  78 测试全过；uvicorn 实启 8099 → `Application startup complete` + 401（auth 正常）后关闭
- 教训：改名 commit 后应跑一次「服务导入链」冒烟（`import agent_server` 或起服务），
  单测不 import 应用入口时这类 F821 会漏网
