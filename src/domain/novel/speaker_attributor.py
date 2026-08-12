"""Cloud / local short-window speaker attributors (Stage D) — eval / experimental."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.novel.speaker_window import AttributionWindow, format_window_prompt

logger = logging.getLogger("agent")

_SYSTEM = """你是说话人分类器。根据上下文判断每条对话的说话人。
规则：
1. 【角色全名映射表】列出了别名→全名的对应关系，归因时 speaker 必须使用全名（不要用别名）。
2. 如果候选说话人中有别名，请查映射表找到对应的全名输出。
3. 无法判断则选「未知」。
4. 不要改写 dialogue 内容，不要新增对话。
5. 只输出 JSON，格式：
{"results":[{"span_id":"...","said_by":"...","confidence":0.0}]}
6. 优先依据：明示「XX说」、叙事主语动作、对话轮替；不要臆造未出现角色。
7. confidence 为 0 到 1 的小数。"""


@dataclass
class AttributionResult:
    span_id: str
    said_by: str
    confidence: float
    raw: str = ""


def _window_as_paragraph(w: AttributionWindow) -> str:
    """Build a short prose paragraph for Haruhi-style extractors."""
    parts: list[str] = []
    if w.context_before.strip():
        parts.append(w.context_before.strip())
    for content in w.target_contents:
        parts.append(f"「{content}」")
    if w.context_after.strip():
        parts.append(w.context_after.strip())
    return "\n".join(parts)


class CloudSpeakerAttributor:
    """Attribute speakers via SharedLLMClient (DeepSeek-compatible)."""

    def __init__(
        self,
        llm_client: Any,
        *,
        temperature: float = 0.0,
        max_tokens: int = 512,
        concurrency: int = 8,
    ):
        self._llm = llm_client
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._concurrency = max(1, int(concurrency))
        self.api_calls = 0
        self.skipped_no_candidates = 0

    async def attribute(
        self, windows: Sequence[AttributionWindow]
    ) -> list[AttributionResult]:
        import asyncio

        self.api_calls = 0
        self.skipped_no_candidates = 0
        if not windows:
            return []
        if self._concurrency <= 1 or len(windows) == 1:
            out: list[AttributionResult] = []
            for w in windows:
                out.extend(await self._attribute_one(w))
            return out

        sem = asyncio.Semaphore(self._concurrency)

        async def _one(w: AttributionWindow) -> list[AttributionResult]:
            async with sem:
                return await self._attribute_one(w)

        batches = await asyncio.gather(*[_one(w) for w in windows])
        out = []
        for part in batches:
            out.extend(part)
        return out

    async def _attribute_one(self, w: AttributionWindow) -> list[AttributionResult]:
        # No real candidates → skip API (model can only return 未知)
        real = [c for c in (w.candidate_speakers or []) if c and c != "未知"]
        if not real:
            self.skipped_no_candidates += 1
            return [
                AttributionResult(span_id=sid, said_by="未知", confidence=0.0, raw="no_candidates")
                for sid in w.target_span_ids
            ]

        user = format_window_prompt(w) + "\n\n请输出 JSON。"
        try:
            raw = await self._llm.achat(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            self.api_calls += 1
        except Exception as e:
            self.api_calls += 1
            logger.warning("Attribution LLM failed for %s: %s", w.window_id, e)
            return [
                AttributionResult(span_id=sid, said_by="未知", confidence=0.0, raw=str(e))
                for sid in w.target_span_ids
            ]

        parsed = _parse_results(raw or "")
        return _align_results(w, parsed, raw or "")


class HaruhiWindowAttributor:
    """Attribute via Haruhi Qwen-1.8B on short windows only (mode=single).

    Does NOT run chapter-level official 600-token chaining.
    """

    def __init__(self, extractor: Any):
        self._extractor = extractor

    async def attribute(
        self, windows: Sequence[AttributionWindow]
    ) -> list[AttributionResult]:
        out: list[AttributionResult] = []
        for w in windows:
            out.extend(await self._attribute_one(w))
        return out

    async def _attribute_one(self, w: AttributionWindow) -> list[AttributionResult]:
        para = _window_as_paragraph(w)
        if len(para) > 1200:
            para = para[:1200]
        try:
            turns = await self._extractor.extract_batch(
                para, chapter_title=w.chapter_title or w.window_id, mode="single"
            )
        except Exception as e:
            logger.warning("Haruhi window failed for %s: %s", w.window_id, e)
            return [
                AttributionResult(span_id=sid, said_by="未知", confidence=0.0, raw=str(e))
                for sid in w.target_span_ids
            ]

        llm_pairs = [(t.speaker, t.content) for t in turns]
        results: list[AttributionResult] = []
        used: set[int] = set()
        for sid, content in zip(w.target_span_ids, w.target_contents):
            idx, speaker, score = _best_content_match(content, llm_pairs, used)
            if idx is not None:
                used.add(idx)
            cands = set(w.candidate_speakers) if w.candidate_speakers else set()
            if cands:
                speaker = _normalize_to_candidates(speaker, cands | {"未知"})
            conf = 0.75 if speaker and speaker != "未知" else 0.0
            if score < 0.35:
                speaker, conf = "未知", 0.0
            results.append(
                AttributionResult(
                    span_id=sid,
                    said_by=speaker or "未知",
                    confidence=conf,
                    raw=f"match={score:.2f}",
                )
            )
        return results


def apply_attribution(
    spans: list,
    results: Sequence[AttributionResult],
    *,
    accept_min: float = 0.5,
) -> list[dict]:
    """Merge attribution into final turn dicts: content unchanged."""
    by_id = {r.span_id: r for r in results}
    turns = []
    for sp in spans:
        if not sp.needs_attribution:
            speaker = sp.speaker_hint
            conf = sp.confidence
            source = "rule:" + sp.hint_source
        else:
            r = by_id.get(sp.span_id)
            if r and r.confidence >= accept_min and r.said_by != "未知":
                speaker, conf, source = r.said_by, r.confidence, "llm_window"
            elif r and r.said_by != "未知":
                speaker, conf, source = r.said_by, r.confidence, "llm_low_conf"
            else:
                speaker = "未知"
                conf = 0.0
                source = "unresolved"
        turns.append(
            {
                "span_id": sp.span_id,
                "speaker": speaker,
                "content": sp.content,
                "confidence": conf,
                "source": source,
                "hint_source": sp.hint_source,
                "needs_attribution": sp.needs_attribution,
            }
        )
    return turns


def candidates_from_text(text: str, *, max_n: int = 12) -> list[str]:
    """Lightweight name harvest from text (for cloud candidates)."""
    from src.domain.novel.dialogue_span import is_noise_speaker

    pat = re.compile(
        r"([\u4e00-\u9fff]{2,3})(?:同学|小姐|先生|桑)?"
        r"(?=说|道|问|看|走|笑|叹|喊|叫|点|咬|抬|站|坐|望|拉|皱|晃)"
    )
    counts: dict[str, int] = {}
    for m in pat.finditer(text or ""):
        name = m.group(1)
        if is_noise_speaker(name) or len(name) < 2:
            continue
        counts[name] = counts.get(name, 0) + 1
    ordered = sorted(counts.keys(), key=lambda k: -counts[k])
    return ordered[:max_n]


def _align_results(
    w: AttributionWindow, parsed: list[dict], raw: str
) -> list[AttributionResult]:
    by_id = {r["span_id"]: r for r in parsed if r.get("span_id")}
    results: list[AttributionResult] = []
    cands = set(w.candidate_speakers)

    for i, sid in enumerate(w.target_span_ids):
        item = by_id.get(sid)
        if item is None and len(parsed) == len(w.target_span_ids):
            item = parsed[i]
        if item is None and len(parsed) == 1 and len(w.target_span_ids) == 1:
            item = parsed[0]
        item = item or {}
        said = str(item.get("said_by") or "未知").strip() or "未知"
        conf = float(item.get("confidence") or 0.0)
        said = _normalize_to_candidates(said, cands) if cands else said
        # LLM 根据映射表输出全名，可能不在原始候选列表中 → 接受
        if cands and said not in cands and said != "未知":
            # 仍做一次宽松匹配：全名包含候选名或候选名包含全名 → 接受
            matched = False
            for c in cands:
                if c != "未知" and (said in c or c in said or said.startswith(c) or c.startswith(said)):
                    matched = True
                    break
            if not matched:
                said, conf = "未知", min(conf, 0.3)
        results.append(
            AttributionResult(
                span_id=sid, said_by=said, confidence=conf, raw=raw[:300]
            )
        )
    return results


def _best_content_match(
    content: str,
    pairs: Sequence[tuple[str, str]],
    used: set[int],
) -> tuple[int | None, str, float]:
    best_i: int | None = None
    best_sp = "未知"
    best_score = 0.0
    c = (content or "").strip()
    for i, (sp, ct) in enumerate(pairs):
        if i in used:
            continue
        t = (ct or "").strip()
        if not t:
            continue
        if c == t or c in t or t in c:
            score = 1.0 if c == t else 0.8
        else:
            inter = len(set(c) & set(t))
            score = inter / max(len(set(c) | set(t)), 1)
            if score < 0.45:
                continue
        if score > best_score:
            best_score = score
            best_i = i
            best_sp = sp or "未知"
    return best_i, best_sp, best_score


def _parse_results(raw: str) -> list[dict]:
    data = _loads_json(raw)
    if data is None:
        return []
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return [x for x in results if isinstance(x, dict)]
        if "said_by" in data:
            return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _loads_json(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None


def _normalize_to_candidates(said: str, cands: set[str]) -> str:
    if said in cands:
        return said
    for c in cands:
        if c == "未知":
            continue
        if said.startswith(c) or c.startswith(said):
            return c
    for suf in ("同学", "小姐", "先生", "さん"):
        if said.endswith(suf):
            base = said[: -len(suf)]
            if base in cands:
                return base
    return said
