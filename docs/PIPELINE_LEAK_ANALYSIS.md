# Pipeline 散落外溢逻辑分析报告

> 日期：2026-08-10 · 范围：src/ 全量 + scripts/ · 方法：调用点扫描 + 分层对比
> 结论先行：**项目有管线（ingest/、dialogue_pipeline/ 等），但管线未收敛为唯一入口，**
> **application 层缺编排服务层，导致 routers / tools / jobs / scripts 四处跨层直连 domain。**

> **实施状态（同日更新）：批次 1-4 已实施完成**（详见第 5 节验收记录）；
> 其中 P3 判断已修正——DialogueExtractor/QAGenerator 非僵尸实现，
> 分别是 ingest 的规则降级路径与 QA 通道主实现，保留。

---

## 1. 理想分层（本应如此）

```
api/routers ──HTTP 适配──▶ application 编排服务（管线）──▶ domain 领域逻辑 ──▶ infrastructure
tools        ──工具适配──▶         ▲
jobs         ──异步执行──▶         │
scripts      ──运维入口──▶   唯一编排入口（管线只此一份）
core         ──代理编排──▶         │
```

- 管线（多步写入/变换：入库、建卡、剧情分析、图谱、角色合并）只应存在于 **application 层**一份
- routers/tools/jobs/scripts 只能调 application 服务，不直接编排 domain 步骤

---

## 2. 现状：管线与调用方实际分布

### 2.1 管线组件实际位置

| 业务域 | 编排函数 | 实际所在层 | 是否应在此 |
|---|---|---|---|
| 入库 | `ingest_novel` | **application**/novel/ingest/coordinator.py | ✅ 唯一正确的示范 |
| 对话抽取 | `extract_dialogue_for_document` | **application**/novel/dialogue_pipeline/extract.py | ✅ 正确 |
| 检索 | `NovelRetrieval.search` | **application**/novel/retrieval.py | ✅ 正确 |
| 剧情分析 | `run_story_analysis` | **domain**/novel/story_analysis/runner.py | ❌ 编排倒置在 domain |
| 建卡 | `enqueue_builds` / `run_build_job` | **domain**/novel/character_on_demand/runner.py | ❌ 编排倒置在 domain |
| 角色合并 | `merge_characters` / `suggest_character_merges` | **domain**/novel/character_merge.py | ❌ 编排倒置在 domain |
| 图谱构建 | `build_graph_rag` | **domain**/novel/graph_rag.py | ❌ 编排倒置在 domain |
| 人物标记 | `_match_known_persons` | **domain**/novel/chunker.py（私有） | ⚠️ 私有，被外部 import |

### 2.2 四处跨层直连的调用方

| 调用方 | 直连的 domain/application 模块数 | 典型直接编排 |
|---|---|---|
| api/routers/novels.py | **19** | ingest_novel、load_analysis/timeline/lorebook、build_graph_rag、purge_series_artifacts、find_orphan_doc_ids |
| api/routers/characters.py | **18** | build_relation_graph、merge_characters、suggest_character_merges、save_alias_map、load_inventory_candidates、enqueue_builds |
| tools/novel_search_handlers.py | **13** | 自己组装入库（_handle_import）；series 解析、channel 格式化 |
| tools/builtin_character.py | 9 | merge_characters、suggest_character_merges、enqueue_builds、load_roster |
| tools/builtin_story.py | 5 | run_story_analysis（domain 编排） |
| tools/builtin_novel_admin.py | 6 | rename_series、purge_series_artifacts（改名清理编排） |
| application/jobs/handlers.py | 直连 | handle_story_analysis→run_story_analysis；handle_graph_rag→build_graph_rag；handle_character_build→run_build_job（还 import 私有 `_from_job_record`/`_save_job`，handlers.py:201-202） |
| core/impersonation/retrieval.py | 10 | 扮演增强检索组装（qa_expand、character_channel_index、narrative_expand） |
| scripts/repair_narrative_characters.py | 私有直连 | `from src.domain.novel.chunker import _match_known_persons`（脚本:24）+ 硬编码 `data/novel_lance` 等路径 |

---

## 3. 外溢点详析（按严重度）

### P1-1 入库存在两条平行路径（行为分叉）

```
路径 A（正规管线）：upload router → ingest_novel
                    转换→分章→narrative/dialogue/qa/character 块→索引→catalog→图谱→LLM 盘点
路径 B（工具重排）：novel_search_handlers._handle_import（tools/novel_search_handlers.py:360-413）
                    preprocess_novel_md + MDCleaner + DialogueExtractor + QAGenerator + index_batch
```

路径 B 问题：
- 绕开 ingest_novel：**无 catalog 记录、无 graph 构建、无 LLM 角色盘点/归一、无 dialogue_pipeline**
- 使用已被取代的旧组件：`src/domain/novel/dialogue.py::DialogueExtractor`、`src/domain/novel/qa_generator.py::QAGenerator`（dialogue_pipeline 用的是 `dialogue_llm.py::LLMDialogueExtractor`）
- 同一本书两条路径入库 → 块结构/通道/元数据不一致，检索与角色管线读到的数据形态不同

### P1-2 编排函数倒置在 domain 层，application 层无服务封装

`run_story_analysis`（story_analysis/runner.py:39）、`enqueue_builds`（character_on_demand/runner.py:206）、
`merge_characters`（character_merge.py:233）、`build_graph_rag`（graph_rag.py）都是**多步编排**
（跨 doc 聚合、LLM 循环、任务队列），却住在 domain 层。

后果：routers（novels.py/characters.py）、tools（builtin_story/builtin_character）、
jobs（handlers.py）三个调用方各自 import domain 编排函数——**编排入口至少有 3 份**，
任何一步调整（如加参数、改降级策略）都要同步三个调用方。

### P2-1 检索组装在 core 层重复

`core/impersonation/retrieval.py`（10 个 import）组装扮演增强检索：qa_expand 判定、
character_channel_index 查询、narrative_expand 展开——与 `application/novel/retrieval.py`
的 channel 逻辑职责重叠。扮演有自己的差异化增强（合理），但"判定/展开"的边界没有收敛，
两个 retrieval 文件对同一套领域函数各自调用，增强点变更需同步维护。

### P2-2 运维脚本复制管线步骤 + 硬编码路径

`scripts/repair_narrative_characters.py`：
- 直接 import 私有函数 `_match_known_persons`（chunker.py:337/431 的内部实现）
- 硬编码 `_LANCE_PATH = Path("data/novel_lance")`、`_ALIAS_DIR`、`_INV_DIR`
- 重复"分块时人物标记"逻辑（本应走 chunker 公开入口或 ingest 步骤）

一次性运维脚本可理解，但说明管线步骤没有可复用公开入口，导致脚本只能复制+硬编码。

### P3-1 旧对话/QA 组件成僵尸实现

`src/domain/novel/dialogue.py::DialogueExtractor`、`src/domain/novel/qa_generator.py::QAGenerator`、
`src/domain/novel/dialogue_local_llm.py` 仅被 tools/_handle_import（平行路径）引用，
dialogue_pipeline 内部已全部切换 `LLMDialogueExtractor`。旧组件是"新管线之外的平行实现"，
保留即鼓励继续绕过管线。

---

## 4. 调用关系图（外溢高亮）

```
                        ┌─────────────────────────────────────────────┐
                        │             调用方（4 处直连）               │
                        │  routers: novels.py(19) characters.py(18)   │
                        │  tools:   novel_search_handlers(13) 等 4 个 │
                        │  jobs:    handlers.py（含私有 import）       │
                        │  scripts: repair_narrative_characters        │
                        └───────┬──────────┬──────────┬───────────────┘
                                │          │          │
            ┌───────────────────▼──┐   ┌───▼──────────────┐
            │ application 层（✅）  │   │ domain 层（❌倒置） │
            │  ingest/             │   │  story_analysis/  │
            │  dialogue_pipeline/  │   │  character_on_demand/
            │  retrieval.py        │   │  character_merge.py
            │  redialogue.py       │   │  graph_rag.py
            └──────────┬───────────┘   └───┬──────────────┘
                       │                    │
                       └────────┬───────────┘
                                ▼
                     domain 纯领域 + infrastructure
```

---

## 5. 修复路线（按依赖顺序，分 4 批）

### 批次 1：收敛入库（消除行为分叉，最高优先级）
- `_handle_import` 改调 `ingest_novel`（ingest 增加 file 路径入口或由调用方读文件传 bytes）
- 旧 `DialogueExtractor`/`QAGenerator` 标注 deprecated（docstring + 入口日志警告）
- 验收：同一文件走两条入口入库，块结构与 catalog 记录一致

### 批次 2：补 application 编排服务层（消除分层倒置）
- 新增 `application/novel/services/`：StoryAnalysisService、CharacterBuildService、
  GraphRagService、CharacterMergeService（薄封装，内部调 domain runner）
- domain 编排函数改为纯领域（或保持，但调用方只经 service）
- routers/tools/jobs 三处改调 service
- 验收：routers/tools/jobs 不再直接 import domain 编排函数

### 批次 3：检索组装收敛 + 脚本上游化
- core/impersonation/retrieval.py 的增强判定（qa/channel/narrative）收敛到 application 扩展点
- repair_narrative_characters.py 改走公开入口（如 chunker 公开 tagging 函数 + series_paths），
  删除硬编码路径

### 批次 4：清理
- 删除/归档旧 dialogue.py、qa_generator.py（确认无其他引用后）
- 全量回归 + 覆盖率复测

---

## 6. 影响面与风险提示

- 批次 1 影响 tools 行为（导入结果会更完整，属于行为变更——需要回归验证）
- 批次 2 是纯重构（行为不变，只改调用路径），风险低但改动面大（3 类调用方）
- 批次 3/4 风险低
- 全程保持现有 149 个需求测试为回归基线（tests/requirements/ 已覆盖各域主路径）


---

## 7. 实施验收记录（2026-08-10）

| 批次 | 内容 | 提交 | 验收 |
|---|---|---|---|
| 1 | 收敛入库：_handle_import 改走 ingest_novel；旧组件标注修正 | f5772bf | 150 passed；回归测试 test_tool_import_goes_through_ingest_pipeline（工具导入产生 catalog） |
| 2 | application 服务层：StoryAnalysis/CharacterBuild/CharacterMerge/GraphRag 4 service | a4bd737 | 5 个调用方（routers×2、tools×2、jobs）不再直连 domain 编排；脚本验证无残留 |
| 3 | 脚本上游化：chunker 新增公开 match_known_persons；repair 脚本改用 series_paths 路径函数 | 本批 | 脚本编译通过，无私有引用、无硬编码路径（_LANCE_PATH 保留，来自 config 默认） |
| 4 | 旧组件判断修正：DialogueExtractor = ingest 规则降级路径；QAGenerator = QA 通道主实现（非僵尸）→ 保留 | 本批 | blocks.py:306/443 确认降级/主路径用途；docstring 已按真实职责修正 |
| 5 | 读取/查询收敛：services 补读 + 新增 Catalog/CharacterQuery + core 层收敛 + 脚本上游化 | 本批 | 见下「批次 5 验收记录」 |

### 批次 5 验收记录（2026-08-10 追加）

**内容**：读取类也全部收敛进 application 服务层（消除「编排走 service、读取仍直连 domain」的新老偏移）。

1. **services 读取封装**：
   - StoryAnalysisService 补 load_analysis / load_timeline / load_lorebook / story_analysis_max_tokens / build_relation_graph（统一双 import 路径：`story_analysis` 与 `story_analysis.config` 两处来源 → 只经 service）
   - 新增 CatalogService：load/list/rename/delete/orphan/ensure_title/purge_series_artifacts
   - 新增 CharacterQueryService：roster / inventory / alias 读写 + sync_alias_roster_save
   - CharacterBuildService 补 get_job / list_jobs；GraphRagService 补 load_graph_rag / is_stale / format_global_context
2. **core 层收敛**：_lorebook.py、impersonation/retrieval.py（expand_narrative_hits ×2）改走 application（新增 src/application/novel/narrative_expand.py 薄转发）
3. **调用方全部改走 service**：routers（novels.py 9 处、characters.py 11 处、helpers.py 1 处）、tools（builtin_character 4、builtin_novel_admin 5、builtin_story 1、novel_search_handlers 3）、jobs/handlers.py 2 处
4. **脚本上游化**：ingest 新增公开 convert_to_md / preprocess_raw_md / convert_epub；character_inventory 新增 as_inventory_character / inventory_config；dialogue 新增 strip_honorific；llm_ner 的 _SYSTEM_PROMPT 改公开 SYSTEM_PROMPT；scripts/dev 12 个脚本改走公开入口，硬编码路径改 series_paths/ROOT

**验收**：routers/tools/jobs/core/shared 对 domain 编排与读取模块零直连（grep 验证）；155 passed；ruff 通过；覆盖率 48.31%（门禁 45）。

### 修正与说明

- **P2-1（检索组装）未强收敛**：core/impersonation/retrieval.py 调用
  application 层的 qa_expand / character_channel_index / narrative_expand
  属于 core（代理编排层）→ application（逻辑层）的**合法分层**；
  扮演增强的差异化是设计内行为，不强合并。列为可选优化（如需，
  可将组合封装为 application 单一入口，但无行为收益）。
- **P3（僵尸组件）判断修正**：dialogue_pipeline 切换后，DialogueExtractor
  未被删除——它是 ingest 在"归因禁用 / LLM 产出 0 块"时的规则回退
  （blocks.py:306），QAGenerator 是 QA 通道主实现（blocks.py:469）。
  保留并标注真实职责；外部新代码走管线入口即可。
- **P1-2 私有 import 消除**：jobs/handlers.py 不再 import
  `_from_job_record`/`_save_job`（由 character_build_service.run_job_from_record 封装）。
