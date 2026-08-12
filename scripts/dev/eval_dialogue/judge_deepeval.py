"""judge_deepeval.py — DeepEval 4.x 适配（原生 DeepSeek 后端）。

ContextualPrecisionMetric：检索上下文排序质量（with reference 变体）。
通过 USE_DEEPSEEK_MODEL=1 + DEEPSEEK_API_KEY 环境变量指向 DeepSeek。
仅对有关键词 gold 的 case 运行。metric 按线程缓存（并发安全）。
"""

from __future__ import annotations

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


def _configure_backend() -> None:
    """DeepEval 原生 DeepSeek 后端（环境变量在 import deepeval 前设置）。"""
    os.environ["USE_DEEPSEEK_MODEL"] = "1"
    os.environ["DEEPSEEK_API_KEY"] = os.getenv("DEEPSEEK_API_KEY", "").strip()
    os.environ["DEEPSEEK_MODEL_NAME"] = (
        os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    )


_metric_cache = threading.local()


def judge_one(query: str, contexts: list[str], reference: str) -> float | None:
    """DeepEval ContextualPrecision，单 case。任何失败返回 None（不中断全量）。"""
    try:
        _configure_backend()
        from deepeval.metrics import ContextualPrecisionMetric
        from deepeval.test_case import LLMTestCase

        if not contexts:
            return 0.0
        metric = getattr(_metric_cache, "metric", None)
        if metric is None:
            metric = ContextualPrecisionMetric(threshold=0.5)
            _metric_cache.metric = metric
        test_case = LLMTestCase(
            input=query,
            actual_output="",  # Retrieval-only：不评生成
            expected_output=reference,
            retrieval_context=contexts,
        )
        metric.measure(test_case)
        return float(metric.score)
    except Exception as exc:  # noqa: BLE001
        print(f"  [deepeval-judge error] {str(exc)[:100]}")
        return None


if __name__ == "__main__":
    s = judge_one(
        "你对库洛艾了解多少",
        ["克萝耶:老师,你要走了吗?", "维尔德拉:库哈哈哈!你看出来了吗,伊芙利特哟"],
        "库洛艾 克萝耶",
    )
    print("deepeval contextual_precision:", s)
