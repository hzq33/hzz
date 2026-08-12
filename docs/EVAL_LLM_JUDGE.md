# Optional LLM Judge（RAGAS / DeepEval）

> ⚠️ **2026-08-06 更新**：旧 `tests/eval/` 已删。当前 LLM judge 入口为 `EVAL_LLM_JUDGE=1 PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/run_judge.py`（自写 DeepSeek 相关性 judge + RAGAS 0.4 + DeepEval 4，实测版本见 `requirements-eval.txt`），结果进 `scripts/dev/eval_dialogue/data/judge_results/`。本文历史命令存档参考。

> Wave B5 — 不挡 PR 主路径；仅在显式开启时运行。  
> 与生产同构评估、离线门禁的分层关系见 [ONLINE_EVAL_DESIGN.md](./ONLINE_EVAL_DESIGN.md)（轨 B）。

## 何时运行

| 场景 | 命令 |
|------|------|
| 本地 | `EVAL_LLM_JUDGE=1 pytest tests/eval/test_ragas_deepeval_optional.py -v` |
| CI 手动 | Actions → **Python CI** / workflow_dispatch，或单独 workflow `eval-llm-judge.yml` |
| Nightly | 可选 cron（默认关闭，避免烧 token） |

依赖：`pip install -r requirements-eval.txt`（RAGAS / DeepEval 等）。

## 环境变量

- `EVAL_LLM_JUDGE=1` — 打开可选用例（否则 skip）
- `DEEPSEEK_API_KEY`（或评估用 LLM key）— judge 调用
- 阈值：`tests/eval/thresholds.json` → `llm_judge`

## 与离线门禁关系

PR 主路径始终跑：

```bash
pytest tests/eval/test_quality_gates.py
```

覆盖 context_precision / faithfulness_proxy / channel_overlap / Hit@K / seed 规模 / speaker gold 标注率。
LLM judge **不是** merge 阻断条件。

**两条路径（勿混淆）**：

| 路径 | 检索后端 | 用途 |
|------|----------|------|
| 夹具 smoke（本文件 pytest） | Mock + 800 块夹具 / synthetic | 验证 RAGAS/DeepEval 接线 |
| 生产对照（设计见 ONLINE_EVAL_DESIGN Phase 2） | Qwen3 + 全量 Lance 命中 | 校准代理指标 vs 语义裁判 |

Retrieval-only Judge **不得**用检索 context 冒充 `answer`；Agent 最终回答的 E2E 评判属 Phase 4。

## 语料

离线门禁与 LLM judge 共用同一份真实语料夹具 `tests/eval/fixtures/real_corpus.json`（约 800 块从已入库《关于我转生变莱姆这档事 维鲁多拉的史莱姆成史观察日记》全卷抽样的 NovelBlocks，仅 narrative + dialogue 两通道），由 `scripts/dev/sample_real_corpus.py` 生成。评估集 `tests/eval/rag_eval_seed.json`（30 条）基于该语料编写。重新抽样后离线指标基线可能漂移，需同步检查 `thresholds.json`。在线生产轨应使用**同源 seed + 全量 Lance**，阈值走独立的 `online_prod` 段（见 ONLINE_EVAL_DESIGN），禁止直接套用离线严谨门槛。
