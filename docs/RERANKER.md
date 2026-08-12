# Qwen3 / Keyword Reranker

## Providers (`novel_rag.reranker_provider`)

| 值 | 行为 |
|----|------|
| `auto`（默认） | 权重目录存在且 transformers 可用 → Qwen3；否则 keyword |
| `keyword` | 确定性词面重叠精排（**CI 默认**） |
| `qwen3` | 强制本地 Qwen3；缺权重时回退 keyword 并打 warning |
| `identity` / `off` | 不精排 |

环境变量：

- `NOVEL_RERANKER_ENABLED=0|1` — 总开关
- `NOVEL_RERANKER_PROVIDER=keyword|qwen3|auto|identity` — 覆盖配置（**CI 设为 keyword**）
- 当 `CI=true` 且未设 `NOVEL_RERANKER_PROVIDER` 时，强制 `keyword`

## 下载 Qwen3-Reranker 权重

```bash
# 示例：ModelScope / HuggingFace 任选其一
mkdir -p models
# modelscope download --model Qwen/Qwen3-Reranker-0.6B --local_dir models/Qwen3-Reranker-0.6B
```

配置：

```yaml
novel_rag:
  reranker_enabled: true
  reranker_provider: qwen3   # 或 auto
  reranker_model_path: models/Qwen3-Reranker-0.6B
  reranker_top_n: 5
```

## 验收

- 有权重机器：`resolve_reranker(provider="qwen3", model_path=...)` → `Qwen3Reranker`
- CI：`NOVEL_RERANKER_PROVIDER=keyword` 或不设（依赖 `CI=true`）→ `KeywordOverlapReranker`
- 质量门禁使用 keyword，不依赖 GPU
