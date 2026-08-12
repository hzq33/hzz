# GraphRAG 全局问答层设计（2026-08）

> 方向：将现有"图谱富集"升级为 GraphRAG 式两层问答（全局语义层 + 实体中心检索）
> 配套：清理 legacy 检索路径（方向 5）
> 状态：待评审

---

## 1. 现状与缺口

| 能力 | 现状 | 缺口 |
|---|---|---|
| 图谱数据 | CharacterGraph（networkx MultiGraph）已修复可生成：节点=角色，边=dialogue/co_occurrence/relation | 仅用于 `_graph_context` 结果富集（弱附加）；无全局语义 |
| 关系事实源 | story_analysis relations 快照（类型/极性/置信度/证据/章节） | 仅前端图谱页 + 时间过滤用 |
| 检索 | multi-channel（narrative/dialogue/qa/character）+ RRF + 精排 | 无"全局综述"类问答（主线/主题/整体关系网） |
| 意图路由 | imitate/relation/character/narrative/question | 无 global 意图 |

**用户可见痛点**：问"这本书主线是什么""谁和谁关系最紧密""整体世界观"时，现有碎片检索答不完整——没有全局视角。

---

## 2. 目标架构（两层问答）

```
Browser → query
         ├─ global 意图（主线/主题/整体）→ GraphRAG 全局层
         │    社区发现（networkx 模块度）→ 社区摘要（LLM）→ 全局上下文
         └─ local 意图（实体/事件/对话）→ 现有 multi-channel 检索
               + 图谱 enrich（已有）+ 关联实体 1-2 跳扩展（新增）
```

### 2.1 全局语义层（global search）

**数据管道**（新模块 `src/domain/novel/graph_rag.py`）：
1. `detect_communities(graph)`：`networkx.algorithms.community.greedy_modularity_communities`（按边权重，relation 边加权优先）→ 角色社区划分
2. `summarize_community(...)`：LLM 按社区生成摘要，含：
   - 社区成员与核心关系网（带关系类型/极性）
   - 主题主线（该社区推动的故事线）
   - 关键事件（引用 story_analysis events 证据）
   - 摘要长度 ~300-500 字/社区，附证据 chapter 引用
3. 存储：`data/graph_rag/{series_id}.json`（communities + summaries + evidence refs + fingerprint）

**检索**（`NovelRetrieval.search_global`）：
- query → 社区摘要关键词/向量匹配（社区数少，线性匹配即可）→ 返回 top 相关社区摘要 + 全局关系总览
- 输出格式：与 `_format_context` 对齐的 `<search_results>` 隔离文本

**触发**：intent_router 新增 `GLOBAL_PATTERNS`（主线|主题|讲了什么|核心人物|整体|世界观|结局|故事梗概|关系网…）

### 2.2 实体中心检索增强（local 增强）

- query 含实体 → 图谱节点定位 → 1-2 跳关联实体集合
- 关联实体片段定向检索（filters 扩展 characters 集合）→ 多跳上下文
- 复用现有 enrich_results + expand_narrative（已修）

### 2.3 构建入口

- story-analysis build job 完成后**联动构建** graph_rag（同 job，附阶段进度）
- 独立重建端点：`POST /api/v1/agent/rag-global/build`（force 重建）
- 读取端点：`GET /api/v1/agent/rag-global?series_id=&query=`

---

## 3. 数据模型

```jsonc
// data/graph_rag/{series_id}.json
{
  "series_id": "败犬女主太多了",
  "fingerprint": "...",          // 与 story_analysis 一致，变更则失效
  "communities": [
    {
      "id": 0,
      "members": ["八奈见杏菜", "温水和彦", "烧盐柠檬", ...],
      "core_relations": [         // 社区内强关系（relation 边优先）
        {"source": "八奈见杏菜", "target": "温水和彦", "relation": "同班/互动", "polarity": "positive", "evidence": [...]}
      ],
      "summary": "以文艺部为中心的日常群像……",   // LLM 生成
      "theme": "青春恋爱与败犬群像",
      "key_events": ["简介：目击被甩", "合宿告白", ...],
      "chapter_span": [1, 31]
    }
  ],
  "global_overview": "全书主线……"    // LLM 生成（可选，跨社区）
}
```

---

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `src/domain/novel/graph_rag.py`（新） | 社区发现 / LLM 摘要 / 存储加载 |
| `src/application/novel/intent_router.py` | 新增 GLOBAL_PATTERNS + global 意图 |
| `src/application/novel/retrieval.py` | `search_global()` + global 输出格式化；local 增加 1-2 跳实体扩展 |
| `src/application/jobs/handlers.py` | story-analysis build 联动 graph_rag |
| `src/api/routers/novels.py` | `GET/POST /rag-global` |
| `src/shared/llm_config.py` | 注册 `graph_rag_summary` endpoint |
| `config.yaml` | `graph_rag` 配置段 |
| 前端 `WorldPage.tsx` | 全局问答输入（可选，第二期） |

## 5. 清理 legacy（方向 5）

| 项 | 处理 |
|---|---|
| `_retrieve_fact_context_legacy` / `_retrieve_style_samples_legacy` | 默认禁用（config `impersonation.legacy_fallback: false`），保留代码防回归 |
| `style_mode=legacy_block` | 移除该分支（已无调用方） |
| 轻链路 `store.search` 单通道回退 | 保留（full chain 异常兜底），不删 |

## 6. 成本与风险

- **LLM 成本**：每系列 1 次 build（社区数 × ~600 token 摘要 + overview）——单次 < 2 万 token，可控
- **延迟**：global 检索无额外 LLM（摘要预生成），仅匹配 → 快
- **风险**：社区划分质量依赖图谱边权重；短文本书（社区=单角色）摘要退化为角色简介——可接受
- **回归**：local 检索链路不动（global 是新增分支）；intent 变化需回归测试

## 7. 实施顺序

1. graph_rag 数据层（社区发现 + 摘要 + 存储）
2. intent_router global 意图 + retrieval.search_global
3. story-analysis 联动 + API 端点
4. legacy 清理（config 关回退）
5. 端到端验证 + 回归测试

---

## 8. 实施记录（2026-08-10）

已完成后端完整链路：

| 项 | 实现 | 验证 |
|---|---|---|
| 社区发现 | `graph_rag.detect_communities`（networkx 模块度贪心，边权重加权；孤立节点自成一社区） | 单测 5 项通过（双社区划分/孤立点/规则摘要回退） |
| 社区摘要 | LLM 批量生成（成员/关系网/主题/关键事件，附章节证据）；失败回退规则摘要 | 败犬 5 社区摘要质量良好 |
| 全局总览 | 跨社区主线概述（社区数 >1 时 LLM 生成） | 主线概述准确 |
| 全局检索 | `format_global_context`：query 角色名/关键词匹配社区摘要，无额外 LLM | GET /rag-global?query= 返回全局上下文 |
| 意图路由 | GLOBAL_PATTERNS（主线/主题/整体/关系网/概括…）→ `is_global` | chat 问主线走全局层 |
| 对话接入 | `NovelRetrieval.search` 命中 global 时前置全局上下文 + 本地碎片 | chat 回复含主线+关系网络+原文引用 |
| API | `GET/POST /rag-global`、`GET /rag-global/jobs/{id}`；story-analysis build 联动构建 | 构建/查询/联动均通 |
| LLM 配置 | `graph_rag_summary` endpoint（设置页可调服务商/模型/key） | 已注册 |
| legacy 清理 | `impersonation.legacy_fallback: false`（默认关回退）；修复 `_story_llm` 缺失的 endpoint 参数 | 全量测试零回归 |

回归：全量 412 passed / 30 failed（既有环境问题，非本次引入）；graph_rag 单测 5 passed。
