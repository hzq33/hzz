# 文档索引

> 同步：2026-08-10 | **状态唯一源**：[AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md](./AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md)

## 现行基线（必读）

| 文档 | 用途 |
|------|------|
| [REQUIREMENTS.md](./REQUIREMENTS.md) | **系统功能需求基线**：A~J 功能域、63 条需求 ID + 验收标准；`tests/requirements/` 按此编写（需求 → 测试） |
| [2026-08_LIVE_TEST_REPORT.md](./2026-08_LIVE_TEST_REPORT.md) | **全功能实测报告**：8 项检索修复（2 字名 / series 过滤 / 运行时索引 / parent 展开…）+ GraphRAG 演进 |
| [2026-08_SECURITY_AND_QUALITY_HARDENING.md](./2026-08_SECURITY_AND_QUALITY_HARDENING.md) | 审计修复 / LLM 盘点切换 / 图谱修复 / P1-P6 流程 |
| [GRAPH_RAG_DESIGN.md](./GRAPH_RAG_DESIGN.md) | **GraphRAG 全局问答层**：社区发现 + 社区摘要 + 全局检索（已实施） |
| [RUNTIME_STABILITY_OPTIMIZATION.md](./RUNTIME_STABILITY_OPTIMIZATION.md) | 框架层稳定性：Job/SSE/LLM/ready（怎么跑稳） |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 运行时架构事实 |
| [AGENT_TOOLS_DESIGN.md](./AGENT_TOOLS_DESIGN.md) | 通用助手工具体系与模块覆盖 |
| [AGENT_FLOW.md](./AGENT_FLOW.md) | Swarm / SSE / 会话流 |
| [STATE_DIAGRAM.md](./STATE_DIAGRAM.md) | 状态机（已并入 AGENT_FLOW，保留作短链兼容） |
| [AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md](./AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md) | Phase 0–3 完成度与 Waves A–E |
| [NOVEL_RAG_DESIGN.md](./NOVEL_RAG_DESIGN.md) | 小说 RAG + 评估门禁 |
| [DEPENDENCY_LOCK.md](./DEPENDENCY_LOCK.md) | lockfile 生成 |
| [MONITORING.md](./MONITORING.md) | 探针 / metrics / 告警 |
| [RERANKER.md](./RERANKER.md) | Qwen3 / keyword 精排 |
| [EVAL_LLM_JUDGE.md](./EVAL_LLM_JUDGE.md) | 可选 LLM judge |
| [RAG_TRACE_EVAL.md](./RAG_TRACE_EVAL.md) | **真实对话检索复盘（trace + LLM 打分 + 结构信号）** |
| [ONLINE_EVAL_DESIGN.md](./ONLINE_EVAL_DESIGN.md) | 在线评估（生产同构 + Judge）设计 |
| [SECURITY_KEY_ROTATION.md](./SECURITY_KEY_ROTATION.md) | A1 密钥轮换清单 |
| [DATA_MODEL_REVIEW_AND_REFACTOR_PLAN.md](./DATA_MODEL_REVIEW_AND_REFACTOR_PLAN.md) | 数据结构评估 + 统一元数据层改进方案（评审稿，未实施） |

## ADR

| 文档 | 主题 |
|------|------|
| [ADR-003](./ADR-003-langgraph-routing.md) | LangGraph 路由 |
| [ADR-004](./ADR-004-impersonation-agent.md) | ImpersonationAgent |
| [ADR-005](./ADR-005-impersonation-session-extensions.md) | 扮演会话扩展（部分 Deferred） |
| [IMPERSONATION_CITATION_FACT_STYLE_SPLIT_DESIGN.md](./IMPERSONATION_CITATION_FACT_STYLE_SPLIT_DESIGN.md) | 扮演出处：口吻/事实拆分与 UI |
| [IMPERSONATION_STYLE_SAMPLE_POOL_DESIGN.md](./IMPERSONATION_STYLE_SAMPLE_POOL_DESIGN.md) | 扮演口吻：角色台词池选型（已验证） |

## 活设计（实现中 / 已落地主路径）

| 文档 | 主题 |
|------|------|
| [DIALOGUE_CHAPTER_EXTRACT_DESIGN.md](./DIALOGUE_CHAPTER_EXTRACT_DESIGN.md) | 对话默认：按章 LLM 抽取（`cloud_chapter`） |
| [DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md](./DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md) | 对话欠采样 + 归因错误修复 |
| [DIALOGUE_IMPORTANCE_TIER_CLEANUP_DESIGN.md](./DIALOGUE_IMPORTANCE_TIER_CLEANUP_DESIGN.md) | 配额升档净化：别名冲突 + 物种黑名单 |
| [NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md](./NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md) | Narrative Child/Parent + Dialogue 配额 |
| [CHARACTER_INVENTORY_DESIGN.md](./CHARACTER_INVENTORY_DESIGN.md) | 角色 inventory + hybrid LLM seed（⚠️ 主路径已切 `ner: "llm"`，见文档头部标注） |
| [MODULE_SPLIT_AND_SEED_HYBRID_WORKLOG.md](./MODULE_SPLIT_AND_SEED_HYBRID_WORKLOG.md) | 拆分修复 / 清理 / 测试重写 / seed hybrid 纪要 |
| [CHARACTER_ON_DEMAND_DESIGN.md](./CHARACTER_ON_DEMAND_DESIGN.md) | 按需建卡 |
| [CHARACTER_CHANNEL_RELATION_EVENT_DESIGN.md](./CHARACTER_CHANNEL_RELATION_EVENT_DESIGN.md) | characters 通道改关系/事件检索（设计） |

## 评测记录

[analysis/](./analysis/) — 历史评测报告，**不是**架构状态源。

## 归档

[archive/](./archive/README.md) — 已淘汰或被取代的设计稿；仅供追溯，勿当作当前默认路径。
