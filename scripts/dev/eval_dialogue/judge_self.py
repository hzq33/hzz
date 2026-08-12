"""judge_self.py — 自写 DeepSeek 相关性 judge（第一性指标）。

判定"检索召回内容与用户输入是否相关"，输出 0-1 分 + 理由。
无框架依赖（仅 openai SDK），作为 RAGAS/DeepEval 的对照基准。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
DEFAULT_BASE_URL = (os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")) + "/v1"

_PROMPT = """你是对话检索质量评估员。请判断「检索到的上下文」是否与「用户输入」相关——即上下文是否能支撑回应这个问题、包含相关的人物/情节/对话信息。

用户输入：{query}
（对话场景角色：{character}）

检索到的上下文（按相关性排序）：
{contexts}

评分标准：
- 1.0：上下文直接包含能回答/回应 query 的关键信息（人物、情节、台词）
- 0.7-0.9：上下文高度相关，包含大部分相关信息
- 0.4-0.6：部分相关（提及相关人物或话题，但缺少核心信息）
- 0.1-0.3：弱相关（只有模糊关联）
- 0.0：完全无关（不同作品、不同话题）

只输出 JSON：{{"score": 0.0-1.0 的分数, "reason": "一句话理由（中文）"}}
"""


def _client():
    from openai import OpenAI

    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")
    return OpenAI(api_key=key, base_url=DEFAULT_BASE_URL)


def judge_relevance(query: str, contexts: list[str], character: str = "", model: str | None = None) -> dict:
    """返回 {"score": float, "reason": str}。contexts 为空时 score=0。"""
    if not contexts:
        return {"score": 0.0, "reason": "无检索结果"}
    ctx_text = "\n".join(f"[{i}] {c[:300]}" for i, c in enumerate(contexts, 1))
    prompt = _PROMPT.format(query=query, character=character or "（无）", contexts=ctx_text)
    client = _client()
    try:
        resp = client.chat.completions.create(
            model=model or DEFAULT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            # 注意：deepseek-v4-flash 为推理模型，response_format=json_object 会与思维链
            # 抢 max_tokens（曾出现空输出 + finish=length），故不用 json_object，靠解析容错
            max_tokens=800,
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw) if raw.strip().startswith("{") else _extract_json(raw)
        score = float(data.get("score", 0.0))
        return {"score": max(0.0, min(1.0, score)), "reason": str(data.get("reason", ""))[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"score": None, "reason": f"judge error: {exc}"}


def _extract_json(text: str) -> dict:
    """从模型输出中容错提取 JSON 对象（思维链包裹等场景）。"""
    import re

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in output: {text[:120]}")
    return json.loads(m.group(0))


if __name__ == "__main__":
    # 自检：单条调用
    q = "你对库洛艾了解多少"
    ctxs = [
        "维尔德拉:哦哦,是智慧之王吧?你是赶来救吾的吧!",
        "克萝耶:老师,你要走了吗?",
    ]
    print(json.dumps(judge_relevance(q, ctxs, "利姆露"), ensure_ascii=False, indent=2))
