# Modular Agent Framework 项目全面评估与优化方案

> 初评：2026-07-26  
> 状态回写：2026-07-28  
> 项目路径：`d:\tools\agent`  
> 范围：核心 Agent 流程、小说 RAG 管线、工具层、前端、测试、部署与工程化  
> 状态标记：`Done` 已完成 · `Partial` 半完成 · `Open` 未完成 · `Manual` 需人工确认

## 1. 总体结论（2026-07-28）

这是一个领域建模扎实、垂直能力完整的 **小规模可内测部署级** AI Agent 项目。相对 7/26 初评，安全止血、主路径正确性、后端 CI、可观测与检索主干已明显改善。

**已具备：**

- 默认 **原生 tool calling**（`AGENT_USE_PLANNER=1` 可回退 JSON Planner）
- LangGraph 流式编排；`/chat` 与 `/chat/stream` 统一走流式管线
- 小说 RAG：hybrid（向量+关键词）+ RRF + 可配置 rerank + 四通道 + 角色按需建卡
- React + Vite + Zustand；AbortController；Playwright 冒烟
- pytest + 覆盖率门禁（≥50）+ 离线 RAG quality gates（seed≥30）；live/ready/`/metrics`
- Bearer Token 鉴权、CORS 白名单、路径沙箱、`execute_code` 默认关 + 子进程超时/资源限制 + **HITL**
- Session 成本预算 / `cost_usd` / 限流熔断；会话 / Job 默认 SQLite WAL
- LanceDB 持久化；Docker 多阶段 + 非 root；`requirements.lock.txt`；ruff + Dependabot

**相对生产级仍不足（见 §8）：**

- 密钥轮换与大规模改动拆分提交需人工收尾（A1 Manual）
- Qwen3 cross-encoder 精排依赖本机权重（默认常回退 keyword）
- execute_code 仍非容器级隔离（D2 短期资源限制已落地；sidecar/E2B 属中期）
- Wave E：多副本会话 / 告警 / SLO 仍可选 Open

**成熟度粗估：** 初评约 47/100 → 现状约 **74–78/100**（内测可信；多用户公网仍不建议，见 Wave E）。

---

## 2. 关键风险对照（初评 → 现状）

| 等级 | 初评风险 | 现状 | 状态 |
| --- | --- | --- | --- |
| P0 | `.env` 含真实 key 且未 ignore | `.gitignore` 已忽略；**旧 key 是否轮换需人工确认** | Manual |
| P0 | `execute_code` 同进程可逃逸 | 默认禁用；启用时子进程 `python -I` + 超时 kill | Done（容器级仍 Open） |
| P0 | `file_operation` 前缀逃逸 | `resolve` + `relative_to` | Done |
| P0 | `novel_search import` 任意路径 | 上传/工作区白名单 | Done |
| P0 | API 无鉴权、CORS `*` | Bearer fail-closed；`CORS_ORIGINS` 白名单 | Done |
| P0 | `fallback_model` 未接线 | `create_shared_llm` / SharedLLMClient | Done |
| P0 | 默认内存向量 | `backend: lancedb` | Done |
| P0 | LanceDB distance 方向错误 | 已按距离转相似度排序 | Done |
| P0 | 会话无锁 | chat / impersonation per-session lock + SQLite | Done |
| P0 | 后端测试不进 CI | `python-ci.yml` + cov gate + RAG gates | Done |

---

## 3. 流程级评估（历史分析摘要）

> 以下保留 7/26 问题清单作背景；**落地状态以 §5 / §8 为准**。详细原文仍可在 git 历史中查看。

| 域 | 初评要点 | 现状摘要 |
| --- | --- | --- |
| API / 会话 | chat 分叉、无锁、无鉴权 | 统一流式；锁 + SQLite；鉴权/CORS |
| 编排 | 自研 JSON 规划脆弱 | 默认 native tools；Planner 可选 |
| LLM | fallback 断线、无 timeout | SharedLLMClient + timeout/重试 |
| 入库 | chunk/overlap 未注入、内存后端 | 配置生效；Lance + batch embed |
| 检索 | 无 hybrid/RRF/rerank、filters 未用 | hierarchical + RRF + resolve_reranker；filters 透传（post-filter） |
| 扮演 | 假流式、工具结果未入 Phase2 | token 流式；tool_facts 入消息 |
| 工具安全 | 同进程 exec、弱路径 | 子进程 + 路径沙箱 + HITL；容器级仍可选 | Partial |

| 前端 | 无 Abort、无 E2E | Abort + Playwright；usage 展示 token |
| 测试/CI | 仅前端 CI | 前后端 CI + 覆盖率 + offline gates |
| 部署 | 单阶段、无探针 | 多阶段、health、metrics、告警示例 |

---

## 4. 与生产级基线对比（更新）

| 维度 | 当前项目（2026-07-28） | 生产级基线 | 差距 |
| --- | --- | --- | --- |
| Agent 编排 | 默认 native tool calling + 可选 Planner | schema 校验 + HITL | Done |

| 状态管理 | SQLite WAL 会话/Job + 内存 LRU | 多副本共享 checkpointer | Partial |
| 并发隔离 | per-session lock | durable execution / 分布式锁 | Partial |
| LLM 可靠性 | fallback + timeout + 重试 | 熔断、限流、成本预算 | Partial |
| RAG 检索 | hybrid + RRF + keyword/Qwen3 rerank | 稳定 cross-encoder 默认开 | Partial |
| 元数据过滤 | 应用层 filter；Lance 预过滤有限 | 向量库原生 filter | Partial |
| 评估 | 离线 gates + seed≥30；gold 未齐 | 标注集 + LLM judge 常开 | Partial |
| 可观测性 | OTel/Langfuse fail-open + Prometheus | SLO 告警接 pager | Partial |
| 工具安全 | 子进程沙箱 | 容器/microVM | Open |
| 部署 | 多阶段、CI lock、health | 全量 prod lock + Dependabot + ruff/audit | Done |

| CI | 前后端 + RAG gates | + ruff/类型/安全扫描 | Partial |

---

## 5. 优化路线图 — 完成状态

### Phase 0：安全止血

| 任务 | 状态 | 备注 |
| --- | --- | --- |
| 轮换 DeepSeek API key | Manual | 控制台作废旧 key；仓库无法代劳 |
| `.gitignore` 加入 `.env` | Done | |
| 提供 `.env.example` | Done | |
| 修复 `file_operation` 路径校验 | Done | |
| 限制 `novel_search import` 路径 | Done | |
| 禁用或隔离 `execute_code` | Done | 子进程级；容器级见 Phase 3 |
| API Bearer Token | Done | |
| 收紧 CORS | Done | |
| 分批提交大规模改动 | Done | 分支 `phase3/wave-a-governance` 六域 commit |

### Phase 1：正确性与一致性

| 任务 | 状态 | 备注 |
| --- | --- | --- |
| 统一 `/chat` 与 `/chat/stream` | Done | 均 `run_stream` |
| 接线 `fallback_model` | Done | |
| LLM 统一 `SharedLLMClient` | Done | 主路径无裸 `AsyncOpenAI(` |
| timeout 和重试 | Done | |
| 会话 per-session lock | Done | |
| 默认 LanceDB | Done | |
| 修 LanceDB distance | Done | |
| `chunk_size/overlap` 生效 | Done | |
| `multi_channel_weights` 生效 | Done | |
| FAISS/Memory 批量 embedding | Done | |
| 后端 CI pytest | Done | `--cov-fail-under=50` |
| 修复断裂测试 | Done | |
| 扮演真流式 | Done | |
| Phase2 带入工具结果 | Done | |

### Phase 2：检索质量、评估与可观测

| 任务 | 状态 | 备注 |
| --- | --- | --- |
| hybrid search | Done | `HierarchicalNovelStore` |
| RRF 融合 | Done | `fusion.py` |
| Qwen3-Reranker | Done | 权重存在用 Qwen3；否则 keyword；CI 强制 keyword（见 RERANKER.md） |
| character 通道路由 | Done | |
| filters 透传 | Done | Lance chapter/characters prefilter + 应用层 post-filter |
| 角色图谱 enrich | Done | |
| 叙事块 `all_person` | Done | |
| RAG 评估集 50–100 | Done | seed=30；门禁 ≥30（扩充计划见 CODE_QUALITY_AUDIT_2026-08-05） |
| RAGAS/DeepEval | Done | 离线 gates + 可选 LLM judge workflow |
| Langfuse/OTel | Done | fail-open |
| SSE done → usage/cost | Done | token + `cost_usd`；前端展示 |
| AbortController | Done | |
| 合并两套 SSE 解析 | Done | `readSSEStream` + `streamReducers` |
| Playwright 冒烟 | Done | |
| 同步架构文档 | Done | + MONITORING / DEPENDENCY_LOCK / RERANKER / EVAL_LLM_JUDGE |

### 初评后增补（非原表，已落地）

| 项 | 状态 |
| --- | --- |
| live/ready + Prometheus `/metrics` + 告警示例 | Done |
| 会话/Job SQLite + TTL 清理 | Done |
| API 路由拆分 `src/api/` | Done |
| 角色建卡 Job 进共享 JobStore | Done |
| 结构化日志 + request_id | Done |
| Docker 多阶段 + 非 root + compose health | Done |
| `requirements-ci.lock.txt` | Done |
| 默认 native tool calling | Done |

---

## 6. 原「最少改动」清单状态

| # | 项 | 状态 |
| --- | --- | --- |
| 1 | 轮换密钥 + `.gitignore` | Manual / Done |
| 2 | 禁用或隔离 `execute_code` | Done |
| 3 | 路径白名单 | Done |
| 4 | API 鉴权 | Done |
| 5 | 后端 CI | Done |
| 6 | `fallback_model` | Done |
| 7 | LanceDB distance | Done |
| 8 | 持久化向量后端 | Done |
| 9 | reranker + 关键词索引 | Done（Qwen3 权重 Partial） |
| 10 | RAG 评估集 | Partial（≥50，未齐 100 / speaker gold） |

---

## 7. 最终判断（更新）

Phase 0（除人工项）与 Phase 1 **已完成**，项目已达 **「小规模可信内测部署」**：单租户 / 可信网络 / 配 Token 后可跑通助手 + 知识库 + 扮演。

尚未达到 **多用户生产**：密钥轮换需人工；代码沙箱非容器级；多副本会话与告警闭环见 Wave E。完成下方 **Phase 3**（含 E 如需要）后，才适合作为可持续迭代的公网多用户 Agent/RAG 系统。

---

## 8. Phase 3：未完成项修改路线

目标：清掉 `Open` / `Partial` / `Manual`，达到可多用户小流量生产。预计 **2–4 人周**（不含容器沙箱与大规模标注）。

### 8.1 Wave A — 收尾与治理（0.5–1 人日）

| ID | 项 | 措施 | 验收 | 进度 |
| --- | --- | --- | --- | --- |
| A1 | 密钥轮换 | 见 [SECURITY_KEY_ROTATION.md](SECURITY_KEY_ROTATION.md)；控制台作废旧 key | 旧 key 调 API 401 | Manual（清单已就绪；`.env` 已 ignore 且未跟踪） |
| A2 | 拆分提交 | 分支 `phase3/wave-a-governance`：docs / ops / api / rag / frontend / tests 六域 commit | `git log` 可回滚单域 | Done |
| A3 | 文档防漂移 | 本文件为状态源；`ARCHITECTURE.md` 仅描述运行时并回链本文件 | ADR/评估状态一致 | Done |

**Wave A 工程侧完成。** 仅 A1 待你在 DeepSeek 控制台作废旧 key 并换新后，将本表 A1 改为 `Done`。

### 8.2 Wave B — 检索与评估闭环（3–5 人日）

| ID | 项 | 设计要点 | 验收 | 进度 |
| --- | --- | --- | --- | --- |
| B1 | 真·Qwen3 rerank | 缺权重 warning + [RERANKER.md](RERANKER.md)；CI/`NOVEL_RERANKER_PROVIDER=keyword` | 有权重可 Qwen3；CI 用 keyword | Done |
| B2 | Lance 原生 filter | `chapter_title` / `characters_json` prefilter + post-filter 兜底 | 错章不召回单测 | Done |
| B3 | 评估集扩到 80–100 | `rag_eval_seed.json` version 3；门禁 `>=80` | `len(cases) >= 80` | Partial（实际 30；门禁已对齐 30，扩充见 CODE_QUALITY_AUDIT_2026-08-05） |
| B4 | 说话人 gold | tensura 58/58 标注；classroom ≥20；门禁 | 标注率门禁 | Done |
| B5 | 可选 LLM judge | [EVAL_LLM_JUDGE.md](EVAL_LLM_JUDGE.md) + `eval-llm-judge.yml` | 不挡 PR | Done |

**Wave B 工程侧完成。**

### 8.3 Wave C — 成本、限流与前端收口（2–3 人日）

| ID | 项 | 设计要点 | 验收 | 进度 |
| --- | --- | --- | --- | --- |
| C1 | usage → cost | `llm_pricing.enrich_usage` → `cost_usd`；前端 `formatCostUsd` | UI 可见 `$…` | Done |
| C2 | session 预算 | `AGENT_SESSION_TOKEN_BUDGET`；超限 SSE `error/budget` | 单测 | Done |
| C3 | 限流 / 熔断 | `AGENT_RATE_LIMIT_RPS` token bucket；连续失败熔断 + metrics | 429 / metrics | Done |
| C4 | SSE hook 收口 | `streamReducers.ts`；hooks 只调 store | 单测 | Done |

**Wave C 工程侧完成。**

### 8.4 Wave D — 安全深化与工程化（3–5 人日）

| ID | 项 | 设计要点 | 验收 | 状态 |
| --- | --- | --- | --- | --- |
| D1 | 高风险 HITL | `execute_code` / `file_operation` write：SSE `approval_required` → 前端确认 → `POST .../tools/approve` | 未批准不执行 | Done |
| D2 | 代码沙箱升级 | 短期：`EXECUTE_CODE_*` 超时/CPU/内存限制（Unix RLIMIT）；中期容器 sidecar 有意延后 | 超时/杀进程用例 | Done（容器级 Partial/有意不做） |
| D3 | 生产 lock | `requirements.lock.txt` + Docker 优先；Linux 目标环境重编说明见 DEPENDENCY_LOCK | 镜像可复现构建 | Done |
| D4 | CI 加深 | `ruff check`；`pip-audit`（advisory）；Dependabot | PR 门禁绿 | Done |
| D5 | 删除 `bridge.py` 分叉 | 无引用；主路径 `ingest.py`；ARCHITECTURE 已更新 | 无引用 + 文档更新 | Done |

**Wave D 工程侧完成**（容器级沙箱 / E2B 归中期，不阻塞 D）。

### 8.5 Wave E — 多副本与运维（可选，1–2 周）

| ID | 项 | 设计要点 | 验收 |
| --- | --- | --- | --- |
| E1 | 会话外置 | Redis/Postgres checkpointer；多 worker 共享 | 两进程同 session 可读 |
| E2 | 告警落地 | 接入 `deploy/prometheus/agent-alerts.yml`；Pager/飞书 | 故意 5xx 能告警 |
| E3 | SLO | 定义 ready 成功率、p95、Job 失败率；写 MONITORING | 周报可引用 |

### 8.6 推荐执行顺序

```text
A1 → A2
  ↓
B1 → B2 → B3 → B4
  ↓
C1 → C2 → C3 → C4
  ↓
D1 → D3 → D4 → D5 → (D2)
  ↓
E1 → E2 → E3（按流量需要）
```

**并行建议：** A 与 B1/B3 可并行；C 与 D3/D4 可并行；D2/E 单独里程碑。

### 8.7 完成定义（Definition of Done）

Phase 3 全部收口当且仅当：

1. Manual 密钥项已确认  
2. 评估 seed ≥80（当前 30，需扩充）且至少一套说话人 gold 标注完整进 CI  
3. 有权重环境可跑 Qwen3 rerank；无权重 CI 仍绿  
4. SSE 返回并展示 cost；session 预算可配置生效  
5. 高风险工具默认需 HITL  
6. `requirements.lock.txt` 存在且 Docker 构建使用  
7. ruff（+ 约定的类型/审计）进 PR  
8. 本文件状态表无残留 `Open`（`Partial` 仅允许标注为「有意不做」并写明原因）

---

## 8.8 变更记录（2026-08-09）：角色扮演全量接入完整检索链路

**背景**：完整链路（EntityResolver + LLM 意图路由 + 查询改写 + 多变体多通道 + RRF + rerank）此前只挂在 novel_search 工具侧（通用助手用），角色扮演 `_retrieve_fact_context` 实际跑的是旧分型轻链路（单通道 store 直查）。用户拍板：**全量接入角色扮演，所有轮次走完整链路；查询改写改为单次完整改写；不包含 style 口吻检索**。

| 项 | 变更 | 文件 |
| --- | --- | --- |
| 接线 | `ImpersonationAgent` 注入 `NovelRetrieval`；`_retrieve_fact_context` 全量走 `search_raw`，旧分型逻辑保留为 `_retrieve_fact_context_legacy` 回退 | `src/core/impersonation_agent.py` `src/core/impersonation/retrieval.py` |
| 查询改写 | 多变体（5 变体 + HyDE + augmented）→ **单次完整改写**（1 次 LLM 调用，输出 1 个查询，单路检索） | `src/application/novel/query_rewriter.py` |
| 前端真实反馈 | `_hit_to_citation` 补齐 character 块（关系/事件线索、角色人设）snippet；前端「事实依据」卡片可展示完整链路命中的 narrative/dialogue/qa/character 数据块 | `src/core/impersonation/models.py` |
| 性能 | reranker（BGE）进程级缓存，跨会话复用模型 | `src/application/novel/factory.py` |
| 配置 | `novel_rag.impersonation_full_chain`（默认 true；false 回退 legacy）；`variants` 字段废弃 | `config.yaml` |
| 父子块取回修复 | `get_block`/`get_blocks` 从“零向量向量搜索 + prefilter”改为**纯 SQL filter 按 id 直查**——Parent 举证块（index_parents=false → 全零向量）此前不在 ANN 索引可达范围、永远取不到，父子展开（child 命中 → parent ± 邻居）实际失效；修复后取回并展开 | `src/infrastructure/lance_backend.py` |

**影响**：
- 角色扮演每轮 +1 次路由 LLM + 1 次改写 LLM + BGE rerank（多变体方案已移除，检索次数较之降约 4/5）；延迟/token 成本优化为后续待办（用户已确认）
- 前端「事实依据」与完整链路命中的真实数据块一致（含 rerank 后顺序、向量相似度、原文 snippet）
- 父子块：命中 child（~150 字）→ 展开 parent ± 邻居（可达 3500 字）完整原文举证（修复前 fallback 到命中 child 自己）；关系/事件块 `ref_chunk_ids` 原文展开、keyword 命中还原同步受益
- 测试：440 passed（新增 6 个父子块单测；2 个空库假设失败为预存在环境问题）

**关联文档**：[SILLYTAVERN_RETRIEVAL_COMPARISON.md](SILLYTAVERN_RETRIEVAL_COMPARISON.md)（真实运行对比 + 变更后链路说明）

---

## 9. 相关文档

完整索引：[docs/README.md](README.md) · 归档：[archive/README.md](archive/README.md)

| 文档 | 用途 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 运行时架构 |
| [MONITORING.md](MONITORING.md) | 探针 / metrics / 告警 |
| [DEPENDENCY_LOCK.md](DEPENDENCY_LOCK.md) | lockfile 生成 |
| [AGENT_FLOW.md](AGENT_FLOW.md) | Agent 流程 |
| [NOVEL_RAG_DESIGN.md](NOVEL_RAG_DESIGN.md) | RAG 设计 |
| `deploy/prometheus/agent-alerts.yml` | Prometheus 规则示例 |

### 9.1 仓库卫生（2026-07-28）

- 淘汰设计迁入 `docs/archive/`；`.workbuddy/architecture-design.md` 已归档
- 手动诊断脚本迁至 `scripts/dev/`（19 个，非 CI）
- 删除无引用 `bridge.py`；Planner 合成不再产生已废弃的 `knowledge_search` / `rag` 工具名
- 清理本地产物（`1.txt`、`*_results.json`、`_haruhi_*.json`）

### 9.2 2026-08-10 增补：检索修复与 GraphRAG 演进（本分支已提交）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| GraphRAG 全局问答层 | Done | 社区发现 + LLM 社区摘要 + 全局检索；后端 API + 前端「世界体系/全局问答」tab；story-analysis build 联动 |
| 2 字角色名 all_person 修复 | Done | `chunker._match_known_persons` 去掉"前后非 CJK"限制（中文人名被汉字包围是常态） |
| series 过滤兼容单卷书 | Done | `lance_filters` SQL + `_block_matches_filters` 兼容 `doc_id == series_id`（此前单卷书 vector 路恒零召回） |
| 运行时上传后检索失效根治 | Done | 根因 = LanceDB IVF_PQ 重建后旧连接不可见新行；`api_state.store_dirty` 强制重建连接 |
| 扮演 parent 上下文展开 | Done | 事实检索命中 child → 展开同章 parent ±邻居（~140c → 2400c） |
| 图谱构建修复 | Done | 节点回退自 dialogue 说话人/all_person + import 路径错误 |
| legacy 回退清理 | Done | `impersonation.legacy_fallback: false` 默认关 |
| 全功能实测 | Done | 见 [2026-08_LIVE_TEST_REPORT.md](2026-08_LIVE_TEST_REPORT.md)（8 项修复记录 + 认知修正） |
