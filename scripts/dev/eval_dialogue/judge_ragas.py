"""judge_ragas.py — RAGAS 0.4.x collections API 适配（Retrieval-only）。

ContextPrecisionWithReference：检索上下文排序质量（reference = gold 文本）。
仅对有关键词 gold 的 case 运行（reference 需要 ground truth）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
BASE_URL = (os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")) + "/v1"

_metric_cache = threading.local()


def _build_metric():
    # thread-local：每个线程独立 metric（AsyncOpenAI client 绑定线程内 event loop）
    metric = getattr(_metric_cache, "metric", None)
    if metric is not None:
        return metric
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import ContextPrecisionWithReference

    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")
    client = AsyncOpenAI(api_key=key, base_url=BASE_URL)
    # deepseek-v4-flash 为推理模型，需调大 max_tokens 供思维链 + 输出（否则 IncompleteOutputException）
    llm = llm_factory(MODEL, client=client, max_tokens=3000)
    _metric_cache.metric = ContextPrecisionWithReference(llm=llm)
    return _metric_cache.metric


def judge_one(query: str, contexts: list[str], reference: str) -> float | None:
    """RAGAS context_precision（with reference），单 case。任何失败返回 None（不中断全量）。"""
    try:
        metric = _build_metric()
        result = asyncio.run(metric.ascore(user_input=query, reference=reference, retrieved_contexts=contexts))
        return float(result.value)
    except Exception as exc:  # noqa: BLE001
        print(f"  [ragas-judge error] {str(exc)[:100]}")
        return None


if __name__ == "__main__":
    # 自检
    s = judge_one(
        "你对库洛艾了解多少",
        ["克萝耶:老师,你要走了吗?", "维尔德拉:库哈哈哈!你看出来了吗,伊芙利特哟"],
        "库洛艾 克萝耶",
    )
    print("ragas context_precision:", s)
