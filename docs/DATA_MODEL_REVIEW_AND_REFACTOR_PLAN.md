# 数据结构评估与统一元数据层改进方案

> **状态**：评审稿（评估完成，**尚未实施**） | 同步：2026-08-11
> **范围**：全项目数据存储结构（LanceDB / SQLite / JSON sidecar / 领域模型）
> **依据**：2026-08 全量代码审计（193 个 Python 文件）+ 运行时数据布局核查

---

## 1. 现状全景

### 1.1 存储介质分布

| 介质 | 路径 | 内容 | 写入方式 |
|------|------|------|---------|
| LanceDB | `data/novel_lance/` | 块级向量库 `novel_blocks`（4 通道向量列 + 元数据列） | 同步 API（已移入工作线程） |
| SQLite (WAL) | `data/jobs/jobs.db` | 异步任务状态（租约/孤儿恢复） | `BEGIN IMMEDIATE` 原子写 |
| SQLite (WAL) | `data/sessions/sessions.db` | 通用/扮演会话消息（namespace 隔离） | `BEGIN IMMEDIATE` 原子写 |
| JSON | `data/catalogs/{series_id}.json` | 书目：系列 / 卷 / 章 / block_counts / 内容指纹 | 直接 `write_text`（部分非原子） |
| JSON | `data/characters/` | 角色卡（`{character_id}.json` / `{series}__{name}.json`） | 直接 `write_text` |
| JSON | `data/rosters/` | L1 角色名录 + 别名白名单（`*.alias.json`） | 直接 `write_text` |
| JSON | `data/inventories/{series_id}.json` | 角色盘点候选（LLM NER / CLUENER） | 直接 `write_text` |
| JSON | `data/story_analysis/` | 剧情分析快照：relations / events / foreshadows / timeline / lorebook | 直接 `write_text` |
| JSON | `data/graphs/` | 角色关系图谱（networkx 序列化） | 显式 UTF-8 写入 |
| JSON | `data/graph_rag/` | GraphRAG 社区摘要 + 全局概览 | 直接 `write_text` |
| JSON | `data/llm_config.json` | LLM 调用点运行时配置（**含明文 API key**） | 直接 `write_text` |
| JSON | `data/sessions/`（可选 json 后端）、`data/jobs/{job_type}/`（可选 json 后端） | 会话 / 任务降级后端 | 原子 rename（session）/ 非原子（job json） |

### 1.2 领域模型（dataclass + to_dict/from_dict）

- `NovelBlock`：单块四通道统一记录（narrative/dialogue/qa/character）
- `NovelDocument` / `Chapter`：入库前结构化中间态
- `CharacterCard` / `CharacterIdentity` / `PersonalityProfile` / `SpeechStyle`
- `DialogueTurn`、`Catalog/Volume/ChapterInfo`、`JobRecord`、`Citation`

### 1.3 关键数据流

```
上传/导入 → ingest 管线（convert→structure→blocks→indexer）
         → LanceDB（向量） + catalog JSON（书目） + roster/inventory（角色） + story_analysis（剧情） + graphs（图谱）
检索     → NovelVectorStore（LanceDB） + hierarchical keyword 索引（内存） + reranker（进程缓存）
角色     → roster（L1） ↔ inventory（候选） ↔ character card（JSON） ↔ graph（图谱） ↔ alias.json（白名单）
```

---

## 2. 做得好的地方（保留基线）

1. **块级统一模型**：`NovelBlock` 一个模型承载四通道，`from_dict` 兼容旧格式（`migrate_to_v2`），演进有明确路径。
2. **持久化工程细节**：session 原子写（tmp+rename）、SQLite `BEGIN IMMEDIATE` + WAL、job 租约 + 孤儿恢复、内容指纹去重（catalog `content_fingerprint`）、`store_dirty` 重建机制、向量索引 `ensure_vector_indices` 幂等重建。
3. **存储按用途分工**：向量 → LanceDB；任务/会话 → SQLite；低频配置与产物 → JSON。分层思路正确。
4. **路径安全**：session_id 正则 + resolve 校验、上传文件名清洗、工作区沙箱路径穿越防护。

---

## 3. 问题与根因

### 3.1 存储介质碎片化（根因：无统一元数据层）

每类业务数据一个 JSON 目录，**跨类操作需要拼多目录**：

- 删除某系列：需依次清理 LanceDB 行、catalog、characters、rosters、inventories、story_analysis、graphs、graph_rag 8 类资产（`delete_novel` / `purge_series_artifacts` 手工枚举，漏一处即产生幽灵数据）。
- 项目历史上反复修复"幽灵卡 / 孤儿卷 / 残留 sidecar / 别名劫持"类 bug，根因即此处。

### 3.2 schema 漂移（典型：`style_tags_json` 超载）

`src/infrastructure/lance_backend.py` `_block_to_row`：为保持 schema 稳定，把 **granularity / parent_id / prev_id / next_id / relationships / ref_chunk_ids / source / background** 五类业务字段全部塞进 `style_tags_json`；读取端 `_row_to_block` 被迫用 `isinstance(raw_tags, dict)` 分支解析两种形态（纯 tags 列表 vs 结构化 payload）。**无版本号、无 schema 声明**，字段靠代码约定累积。

### 3.3 一致性靠约定而非约束

同一角色的数据分散 5 处（roster / inventory / character card / graph / alias.json），对齐方式靠"doc_id / series_id 派生"手工约定（`series_id_from_doc_id`、`character_id_for`）。无外键、无级联、无校验——曾出现"幽灵卡（错误系列）"、`_NAME_INDEX` 缓存失效需手工 `invalidate_card_index` 等问题。

### 3.4 JSON 无 schema 校验

所有 JSON sidecar 的 `from_dict` 均以 `data.get(key, default)` 静默容错：坏数据不报错、只降级（如空卡占位），**排查成本高**；部分 JSON 写入非原子（catalog/characters 等直接 `write_text`，崩溃可能写坏）。

### 3.5 弱项清单

| 弱项 | 位置 | 说明 |
|------|------|------|
| API key 明文 | `data/llm_config.json` | 单租户可信环境下的妥协，仍属风险点 |
| JSON job 后端竞态 | `src/shared/async_jobs.py` `claim_next_pending` | read-modify-write 非原子（注释自认"single-process safe enough for tests"）；生产默认 sqlite 不受影响 |
| `characters` 与 `all_person` 合并读回 | `lance_backend._row_to_block` | 存储时合并单列，读回语义混同（当前消费方均为"人名集合"用途，无实际损失） |
| 缓存失效手工化 | `CharacterCard._NAME_INDEX`、keyword 索引、`store_dirty` | 各缓存各自为政，无统一失效机制 |

---

## 4. 改进方案：统一元数据层（推荐）

**核心思路**：把高频、需关联、需一致的**元数据**（书目/卷/章、角色名录、盘点、剧情分析索引）收敛进一个 SQLite 库（`data/meta.db`，WAL）；JSON 只保留"大 blob 产物"（图谱文件、分析全文、角色卡正文）；**LanceDB 向量表不动**。读取层通过统一 Repository 存取，写入层逐步收敛。

### 4.1 目标架构

```
业务模块 ──→ MetaRepo（统一存取层，单点读写）
                 ├── SQLite meta.db：series / volumes / chapters / roster / inventory / analysis / assets
                 └── JSON 产物（仅大 blob：图谱、分析全文、角色卡）
向量检索 ──→ LanceDB novel_blocks（不变）
```

### 4.2 目标 Schema（示例）

```sql
CREATE TABLE series (
  series_id TEXT PRIMARY KEY,
  series_title TEXT, created_at TEXT, updated_at TEXT, schema_version INTEGER
);
CREATE TABLE volumes (
  doc_id TEXT PRIMARY KEY,
  series_id TEXT NOT NULL REFERENCES series(series_id) ON DELETE CASCADE,
  volume_no INTEGER, title TEXT, source_format TEXT,
  indexed_at TEXT, fingerprint TEXT, needs_reindex INTEGER, block_counts_json TEXT
);
CREATE TABLE chapters (
  doc_id TEXT, chapter_id TEXT, title TEXT,
  order_no INTEGER, char_count INTEGER,
  PRIMARY KEY (doc_id, chapter_id)
);
CREATE TABLE roster_entries (
  series_id TEXT, character_id TEXT, name TEXT, aliases_json TEXT,
  mention_count INTEGER, PRIMARY KEY (series_id, character_id)
);
CREATE TABLE inventory (series_id TEXT PRIMARY KEY, payload_json TEXT, updated_at TEXT);
CREATE TABLE analysis (
  series_id TEXT, kind TEXT, updated_at TEXT,
  payload_path TEXT, stale INTEGER, PRIMARY KEY (series_id, kind)
);
-- sidecar 资产清单：purge / 孤儿校验单点化
CREATE TABLE assets (series_id TEXT, kind TEXT, path TEXT);
```

### 4.3 方案对比

| 选项 | 收益 | 风险 | 结论 |
|------|------|------|------|
| 不动 | — | 碎片化 / schema 漂移持续累积 | ✗ |
| 仅加 JSON 校验钩子 | 低（只堵数据损坏） | 低 | 可作 P0，但不解决碎片化 |
| 全量 SQLite 化（含向量库） | 中 | 高（大爆炸式重构） | ✗ |
| **统一元数据层（本方案）** | 高（幽灵数据根治、一致性约束化、schema 演进友好） | 低-中（双写过渡 + 模块独立迁移） | ✓ |

### 4.4 关键收益

1. **根治幽灵数据**：删系列 = `DELETE FROM series` + 外键级联 + `assets` 清单清理，单点操作。
2. **约束替代约定**：PRIMARY KEY / 外键由数据库保证，角色数据 5 处分散对齐问题根除。
3. **schema 演进友好**：`ALTER TABLE` + 版本迁移，替代 JSON 手工兼容分支。
4. **附带**：统一原子写（`BEGIN IMMEDIATE`）、跨类查询一条 SQL、启动孤儿校验可落成 SQL 扫描。

---

## 5. 分阶段实施计划

| 阶段 | 内容 | 改动范围 | 验收标准 | 回滚 |
|------|------|---------|---------|------|
| **P0** | JSON 统一原子写工具（tmp+rename）+ `schema_version` 字段 + 读取校验钩子 | `src/shared/json_io.py`（新）+ 各 JSON 写入点 | 全部 JSON 写入原子化；坏文件读取报错可定位 | 工具函数替换，低风险 |
| **P1** | `meta.db` 建表 + `MetaRepo` + **catalog 迁移**（双写 → 只写新库） | `catalog_service.py`、`catalog.py` | 书目 CRUD 全走 repo；指纹去重 / needs_reindex 行为不变 | 读优先新库，回退删表即可 |
| **P2** | roster / inventory 迁移 | `character_query_service.py`、ingest `build_inventory` | 角色名录与盘点读写收敛；alias.json 白名单保留 | 同 P1 |
| **P3** | story_analysis / graph_rag 索引迁移 + **purge 单点化** | `story_analysis`、`graph_rag`、`purge_series_artifacts` | 删系列一条 SQL + assets 清单清理，无残留 | 同 P1 |
| **P4** | 启动孤儿校验 + 告警（SQL 扫描 vs LanceDB doc_ids 比对） | `agent_server` 启动维护 | 启动日志报告孤儿资产；可一键清理 | 纯新增，可关 |
| **P5**（延后） | LanceDB 独立 `all_person_json` 列（带迁移）；`llm_config.json` 密钥移出明文 | `lance_backend`、`llm_config` | 语义分离；密钥落 .env / 加密 | 需要时单独排期 |

**依赖关系**：P0 → P1 → P2 → P3 串行（同一 repo 演进）；P4 依赖 P1-P3 完成；P5 独立。

---

## 6. 迁移策略与风险控制

1. **双写过渡**：每个模块迁移时先"读走新库 + 写同时写 JSON 与 SQLite"→ 用差异比对脚本验证一致 → 切"只写新库"。
2. **启动一次性导入**：旧 JSON 在首次启动时导入 meta.db，导入成功标记，幂等可重跑。
3. **不动 LanceDB 向量表**：核心检索数据零风险；`all_person` 分离（P5）另走迁移脚本。
4. **每阶段独立提交、独立回滚**：不出现跨模块的大爆炸提交。
5. **兼容层**：保留现有 `load_catalog` / `load_roster` 等函数签名，内部切 repo，调用方零改动。

---

## 7. 收益评估（预估）

| 指标 | 现状 | 完成后 |
|------|------|--------|
| 删除系列操作 | 手工枚举 8 类资产，易漏 | 一条 SQL + 级联 + assets 清单 |
| 数据一致性保障 | 代码约定（易漂移） | 外键 / PRIMARY KEY 约束 |
| schema 演进 | JSON 手工兼容分支 | ALTER TABLE + 版本迁移 |
| 写入原子性 | 部分 JSON 非原子 | 全部原子（SQLite / tmp+rename） |
| 幽灵数据问题 | 历史上多次修复 | 从根上消除 + 启动扫描兜底 |

---

## 8. 附录：与本次代码审计的关系

本次审计已先行修复的数据问题（已完成，见提交 `5c33965`）：

- LanceDB `index()` 单条 delete SQL 转义（防注入/语法错误）
- 向量维度不匹配显式报错（原静默写全零向量）
- `iter_blocks` 列投影（12k 块扫描 19s → ~1s）
- 扮演 store 与 API 共享 `api_state.imp_store`（根治运行时上传后扮演检索失效）
- 上传流式落盘 + 增量 413 校验

本报告提出的 P0-P5 为**后续结构性演进**，与上述修复互补，暂未实施。
