# Novel RAG Design

> 同步日期：2026-07-29 | 状态：Current（2026-08-06 更新：离线 `tests/eval/` 已删，评估统一走 `scripts/dev/eval_dialogue/`，见 [DIALOGUE_RETRIEVAL_EVAL_DESIGN.md](./DIALOGUE_RETRIEVAL_EVAL_DESIGN.md)）
> 检索分层细节见 [NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md](./NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md)；精排见 [RERANKER.md](./RERANKER.md)

## 管道

```
EPUB/TXT/MD → 预处理（分章/repair/normalize）→ Chunk（Narrative Parent/Child + Dialogue）
            → Extract（Inventory + 对话归因）→ Embed → Index (LanceDB|FAISS)
                                                              │
Query → IntentRouter → Hybrid(vector+keyword) → RRF → [Rerank] → Context
                         │
                    optional CharacterGraph enrich
```

## 通道

| Channel | 用途 |
|---------|------|
| narrative | 场景 / 情节 |
| dialogue | 口吻模仿 |
| qa | 事实问答（**当前未启用**：全库 qa 块为 0，`config.yaml → novel_rag.qa.enabled: false`；启用需上传时 `generate_qa=true`） |
| character | 人设 / 说话风格（已重定位为关系/事件索引，见 CHARACTER_CHANNEL_RELATION_EVENT_DESIGN） |

`IntentRouter` 输出 `primary_channel` + `channel_weights`；character 通道参与路由。

## 存储

- **默认**：LanceDB 持久化（`./data/novel_lance`）
- **单测 / CI**：FAISS + `MockEmbeddingProvider`
- Metadata filters（doc_id / 角色 / 章节）：Lance 原生 prefilter + 应用层 post-filter 兜底
- Narrative 层级：`granularity=parent`（举证，默认不写向量）/ `granularity=child`（检索，写向量），见 [NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md](./NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md)

## 质量门禁（CI）

> ⚠️ **2026-08-06 更新**：旧 `tests/eval/` 全套（thresholds / real_corpus / rag_eval_seed / quality_gates）已删除且不恢复。当前评估入口为 `scripts/dev/eval_dialogue/`（L1 seed 构建 → L2 代理指标 → L3 LLM judge 对比 → L4 报告），设计与指标定义见 [DIALOGUE_RETRIEVAL_EVAL_DESIGN.md](./DIALOGUE_RETRIEVAL_EVAL_DESIGN.md)，报告落盘 `docs/analysis/dialogue_eval/`。下表为历史阈值，仅存档参考。

历史阈值（存档）：

| 指标 | 含义 | 默认下限 |
|------|------|----------|
| `context_precision` | 检索上下文命中 `must_include_any` | 0.80 |
| `faithfulness_proxy` | 命中 `block_type` 与 `expected_channels` 重叠（无 LLM judge 时的忠实度代理） | 0.75 |
| `channel_overlap` | 路由通道与标注重叠 | 0.85 |
| `hit_at_k` | Hit@5（全量真实语料 case 集），`minimum_ratio` | 0.80 |

> **2026-08-10 更新**：旧 `tests/eval/` 离线门禁（quality_gates / 800 块真实语料夹具 /
> rag_eval_seed / thresholds / speaker_gold）已随测试套件重写删除，**不恢复**——
> 依赖真实《转生史莱姆》语料，与"合成 fixture、不绑定具体书籍"的测试原则冲突。
> 当前评估入口见 [DIALOGUE_RETRIEVAL_EVAL_DESIGN.md](./DIALOGUE_RETRIEVAL_EVAL_DESIGN.md)
> （`scripts/dev/eval_dialogue/`）。上表离线指标门槛为历史值，仅存档参考。

## 相关文档

- [ARCHITECTURE.md](./ARCHITECTURE.md) — 运行时架构
- [NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md](./NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md) — Narrative Parent/Child + Dialogue 配额
- [DIALOGUE_CHAPTER_EXTRACT_DESIGN.md](./DIALOGUE_CHAPTER_EXTRACT_DESIGN.md) — 按章对话抽取
- [RERANKER.md](./RERANKER.md) — Qwen3 / keyword 精排
- [EVAL_LLM_JUDGE.md](./EVAL_LLM_JUDGE.md) — 可选 LLM judge
- [ONLINE_EVAL_DESIGN.md](./ONLINE_EVAL_DESIGN.md) — 在线评估设计（生产同构 + Judge）
