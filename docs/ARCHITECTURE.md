# Architecture — Modular Agent (Makers)

> 同步日期：2026-07-28 | 状态：Current  
> **工程优化完成度**以 [AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md](./AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md) 为唯一状态源（Done / Partial / Open / Manual）；本文件只描述运行时事实。

本文档描述**当前仓库真实架构**。旧双轨 RAG 诊断文已归档至 [archive/architecture-design.md](./archive/architecture-design.md)；其中的 `rag/`、`upload_serve/`、`RAG_Parser_Serve/` 代码路径已删除，请勿再引用。

## 1. 定位

模块化 AI Agent + Novel RAG + 角色扮演：

| 能力 | 入口 | 运行时 |
|------|------|--------|
| 通用助手 | `POST /api/v1/agent/chat[/stream]` | `SwarmAgent` → Plan→Execute→Reply |
| 角色扮演 | `POST /api/v1/agent/impersonate/chat[/stream]` | `ImpersonationAgent` + 4 通道检索 |
| 知识库 UI | Frontend `/#/knowledge` | 角色工作台（roster→勾选建卡）+ 按需剧情脉络 |
| 书目 | `GET/DELETE /api/v1/agent/novels` | Catalog sidecar（系列/卷/章节序） |
| 剧情分析 | `POST /api/v1/agent/story-analysis/build` | 按需 map/reduce → **关系与事件检索索引**（默认不抽伏笔；有限并发；单卷 merge；Job progress；character 通道） |

## 2. 运行时拓扑

```
Browser (React HashRouter)
    │  Vite/nginx 注入 Bearer
    ▼
agent_server.py (FastAPI + SSE)
    │  auth fail-closed (AGENT_API_TOKEN)
    ▼
ConversationService ── per-session lock
    ▼
SwarmAgent (LangGraph)
    classify ─┬─ general: plan → execute → reply
              └─ character: rag_enter → rag_chat → exit_role
```

## 3. 包结构（`src/`）

| 包 | 职责 |
|----|------|
| `core/` | `Agent`, `SwarmAgent`, `ImpersonationAgent`, Planner, Executor, Memory |
| `application/` | `ConversationService`, Novel ingest/retrieval/factory, event bus |
| `domain/` | `NovelBlock`, CharacterCard, chunker, dialogue extractor |
| `infrastructure/` | Vector store (LanceDB/FAISS), hybrid+RRF, embedding, reranker, graph |
| `tools/` | Registry + builtin tools（`novel_search`/`character_kb`/`story_analysis`/`novel_admin`；`execute_code` 默认禁用）。设计见 [AGENT_TOOLS_DESIGN.md](./AGENT_TOOLS_DESIGN.md) |
| `shared/` | `SharedLLMClient`, telemetry (OTel/Langfuse), session store |
| `utils/` | auth, config, errors, logger |

已删除：`rag/`、`upload_serve/`、`RAG_Parser_Serve/`。

## 4. Novel RAG

- **默认后端**：LanceDB（`config.yaml` → `novel_rag.backend: lancedb`）；单测常用 FAISS + MockEmbedding
- **四通道**：narrative / dialogue / qa / character
- **检索**：`hybrid_search`（向量 + 关键词）+ RRF；可选 `graph_enrich`、Qwen3-Reranker
- **评估**：评测记录在 `docs/analysis/`（历史报告）；`tests/eval/` 门禁已随测试套件重写移除（2026-08-05），评估统一走 `scripts/dev/eval_dialogue/`（不重建，见 [DIALOGUE_RETRIEVAL_EVAL_DESIGN.md](./DIALOGUE_RETRIEVAL_EVAL_DESIGN.md)）

## 5. 安全与可观测

| 项 | 行为 |
|----|------|
| `AGENT_API_TOKEN` | 未设置 → 非 public 返回 503；错误 token → 401 |
| CORS | `CORS_ORIGINS` 显式白名单 |
| 路径沙箱 | `file_operation` / `novel_search import` 限制工作区 |
| `execute_code` | `EXECUTE_CODE_ENABLED=false` 默认关闭；启用时子进程 `python -I` + 超时/CPU/内存限制 + **HITL** |
| HITL | `AGENT_TOOL_HITL=1`（默认）：`execute_code` / `file_operation` write → SSE `approval_required` → `POST .../tools/approve` |
| Rate limit | `AGENT_RATE_LIMIT_RPS`；LLM 熔断 metrics |
| Session budget | `AGENT_SESSION_TOKEN_BUDGET`；usage 含 `cost_usd` |
| Telemetry | `TELEMETRY_ENABLED` + OTel；可选 Langfuse（`LANGFUSE_*`），fail-open |
| Probes | `/api/v1/agent/health/live`（存活）、`/ready`（就绪，缺配置/Token → 503） |
| Metrics | `GET /metrics`（Prometheus；生产环境请网络隔离）；告警示例见 [MONITORING.md](MONITORING.md) |
| 数据保留 | 启动时清理会话（`AGENT_SESSION_KEEP`）与过期 Job（`AGENT_JOB_TTL_HOURS`） |
| 会话存储 | 默认 SQLite WAL（`AGENT_SESSION_BACKEND=sqlite`），`chat`/`imp` 分 namespace；可回退 `json` |
| Job 存储 | 默认 SQLite WAL（`AGENT_JOB_BACKEND=sqlite`）；角色建卡 `character_build` 与剧情 Job 共用；可回退 `json` |
| Readiness | `/ready` 检查 Token/配置/data 可写/会话库/Job 库/Lance 路径 |
| 日志 | `AGENT_LOG_FORMAT=json` 输出结构化日志；中间件注入 `request_id` |
| Reranker | 默认 `reranker_enabled: true`；`auto` 有权重用 Qwen3，否则 keyword（CI 可用） |

## 6. 前端

- React 18 + Vite + Zustand + Tailwind
- 路由：`/` 通用助手、`/impersonation`、`/knowledge`（角色工作台 + 剧情脉络）、`/tools`
- Knowledge：系列/卷选择、上传、勾选按需建卡、Job 轮询、一键扮演；剧情脉络按需生成时间线/伏笔/关系（带原文证据）
- SSE：统一解析 + AbortController
- E2E：Playwright 冒烟 + mock 主路径（聊天/知识库/扮演）

## 7. CI / 部署

| Workflow | 内容 |
|----------|------|
| `python-ci.yml` | ruff（关键）+ pytest + `--cov-fail-under=50` + RAG gates + pip-audit（advisory） |
| `frontend-ci.yml` / `frontend-pr.yml` | lint / typecheck / coverage / build / Playwright |
| `backend-docker.yml` | 后端镜像 + live/metrics smoke |
| Dependabot | pip / npm / github-actions 周更 |

Compose：`agent-data` 卷持久化 `/app/data`；后端非 root（uid 10001）；前后端 HEALTHCHECK；`depends_on: service_healthy`。

## 8. API 模块拆分

| 包 | 职责 |
|----|------|
| `src/api/schemas.py` | 请求/响应模型 |
| `src/api/state.py` | 会话/扮演运行时状态 |
| `src/api/helpers.py` | 角色/系列辅助函数 |
| `src/api/routers/ops.py` | health / metrics / web-vitals |
| `src/api/routers/chat.py` | 通用助手 chat / history / tools |
| `src/api/routers/approvals.py` | HITL 工具审批 `POST .../tools/approve` |
| `src/api/routers/impersonation.py` | 角色扮演 SSE |
| `src/api/routers/characters.py` | 角色列表 / 建卡 / 编辑 |
| `src/api/routers/novels.py` | 上传 / 书目 / 剧情分析 |
| `src/application/impersonation_sessions.py` | 扮演会话锁 + LRU + 落盘 |
| `src/shared/llm_factory.py` | 统一 primary/fallback LLM 构造 |
| `src/shared/tool_approvals.py` | 高风险工具 HITL 门闩 |

小说入库主路径为 `src/application/novel/ingest/` 包（旧 `bridge.py` / 单体 `ingest.py` 已删除）。

契约导出：`python scripts/export_openapi.py` → `docs/openapi.json`。

## 9. 相关文档

完整索引：[README.md](./README.md)

- [AGENT_FLOW.md](./AGENT_FLOW.md) — 对话与 Swarm 流转
- [NOVEL_RAG_DESIGN.md](./NOVEL_RAG_DESIGN.md) — 检索与评估
- [AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md](./AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md) — **优化完成度状态源** + Phase 3 路线
- [SECURITY_KEY_ROTATION.md](./SECURITY_KEY_ROTATION.md) — 密钥轮换清单（A1）
- [MONITORING.md](./MONITORING.md) — 探针 / metrics / 告警
- [DEPENDENCY_LOCK.md](./DEPENDENCY_LOCK.md) — 依赖锁定
- [RERANKER.md](./RERANKER.md) — Qwen3 / keyword 精排
- [EVAL_LLM_JUDGE.md](./EVAL_LLM_JUDGE.md) — 可选 LLM judge
- [archive/](./archive/README.md) — 已淘汰设计稿
