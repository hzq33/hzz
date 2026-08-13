"""On-demand character builder — name normalization, evidence gathering, distill.

Extracted from the former monolithic ``character_on_demand.py``; logic unchanged.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from src.domain.novel.character_on_demand.models import (
    EvidencePack,
    NormalizeResult,
)
from src.domain.novel.character_roster import (
    CharacterRoster,
    character_id_for,
    load_roster,
)

logger = logging.getLogger("agent")

_MIN_DIALOGUES_DEFAULT = 5
_MAX_DIALOGUE_SAMPLES = 60
_MAX_NARRATIVE_SAMPLES = 20

_SINGLE_SYSTEM = """你是日轻小说角色分析专家。只分析【一个】指定角色，输出该角色的完整档案 JSON。

## 输出格式（严格遵守字段名，不要修改）

只输出一个 JSON 对象（不要用角色名做外层 key）：
{
  "traits": {
    "extraversion": 0.0到1.0,
    "agreeableness": 0.0到1.0,
    "conscientiousness": 0.0到1.0,
    "neuroticism_reverse": 0.0到1.0,
    "dominance": 0.0到1.0,
    "complexity": 0.0到1.0
  },
  "speech": {
    "vocabulary": "用词特点",
    "sentence_pattern": "句式特点",
    "catchphrase": "口头禅",
    "emotional_expression": "情绪表达方式",
    "rhythm": "语调节奏"
  },
  "catchphrases": ["口头禅1", "口头禅2"],
  "emotional_tendencies": "主要情绪倾向",
  "role": "角色定位",
  "personality": "性格概述（2-4句，必须依据证据）",
  "background": "背景概述（依据证据；不知则写文中未体现）",
  "relationships": "人际关系（依据证据）"
}

## 重要
- traits / speech 字段名不得改名
- 只分析目标角色；证据里其他人只作关系背景，禁止输出他人档案
- 结论必须能被证据支持；不要编造未出现情节
- 只输出 JSON，不要其他文字"""



def normalize_name(
    input_name: str,
    *,
    series_id: str,
    roster: CharacterRoster | None = None,
    resolve_character_id: str | None = None,
) -> NormalizeResult:
    """Corpus-only normalize (D1: no web). Map alias → canonical via AliasMap + roster."""
    raw = (input_name or "").strip()
    if not raw:
        raise ValueError("input_name required")

    roster = roster or load_roster(series_id)

    # Prefer series AliasMap (honorific / observed alias merge)
    try:
        from src.domain.novel.alias_map import load_alias_map
        amap = load_alias_map(series_id)
        if amap and not resolve_character_id:
            hit = amap.resolve(raw)
            if hit:
                aliases = sorted(set([hit.canonical_name, *hit.aliases, raw]))
                return NormalizeResult(
                    character_id=hit.character_id or character_id_for(series_id, hit.canonical_name),
                    canonical_name=hit.canonical_name,
                    aliases=aliases,
                )
    except Exception:
        pass

    if resolve_character_id and roster:
        for e in roster.characters:
            if e.character_id == resolve_character_id or character_id_for(series_id, e.name) == resolve_character_id:
                aliases = sorted(set([e.name, *e.aliases_observed, raw]))
                return NormalizeResult(
                    character_id=e.character_id or character_id_for(series_id, e.name),
                    canonical_name=e.name,
                    aliases=aliases,
                )

    if not roster or not roster.characters:
        cid = character_id_for(series_id, raw)
        return NormalizeResult(character_id=cid, canonical_name=raw, aliases=[raw])

    # Strip honorific for matching
    base = raw
    m = re.fullmatch(r"(.+?)(大人|桑|君|酱|醬)$", raw)
    if m:
        base = m.group(1)

    scored: list[tuple[float, Any]] = []
    for e in roster.characters:
        score = 0.0
        if raw == e.name or raw in e.aliases_observed:
            score = 1.0
        elif base == e.name or base in e.aliases_observed:
            score = 0.98  # honorific strip → prefer canonical base
        elif raw in e.name or e.name in raw:
            score = 0.7
        elif any(raw in a or a in raw for a in e.aliases_observed if len(a) >= 2):
            score = 0.65
        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda x: (-x[0], -x[1].dialogue_count))
    if not scored:
        cid = character_id_for(series_id, raw)
        return NormalizeResult(character_id=cid, canonical_name=raw, aliases=[raw])

    # Prefer non-honorific canonical when scores are close
    top_score, top = scored[0]
    if len(scored) >= 2 and (top_score - scored[1][0]) < 0.05:
        # pick higher dialogue_count among near-ties
        near = [x for x in scored if top_score - x[0] < 0.05]
        near.sort(key=lambda x: (-x[1].dialogue_count, -x[0], len(x[1].name)))
        top_score, top = near[0]

    if len(scored) >= 2 and (top_score - scored[1][0]) < 0.08 and scored[1][0] >= 0.65:
        # still ambiguous only if different bases
        if scored[1][1].name != top.name and scored[1][1].name not in top.aliases_observed:
            return NormalizeResult(
                character_id="",
                canonical_name=raw,
                aliases=[raw],
                need_disambiguate=True,
                candidates=[
                    {
                        "character_id": e.character_id or character_id_for(series_id, e.name),
                        "canonical_name": e.name,
                        "score": round(s, 3),
                        "dialogue_count": e.dialogue_count,
                    }
                    for s, e in scored[:5]
                ],
            )

    aliases = sorted(set([top.name, *top.aliases_observed, raw, base]))
    # Drop tiny aliases
    aliases = [a for a in aliases if len(a) >= 2]
    cid = top.character_id or character_id_for(series_id, top.name)
    return NormalizeResult(
        character_id=cid,
        canonical_name=top.name,
        aliases=aliases,
    )


def _speaker_match(speaker: str, name_set: set[str]) -> bool:
    sp = (speaker or "").strip()
    if not sp:
        return False
    if sp in name_set:
        return True
    for n in name_set:
        if len(n) >= 2 and (n in sp or sp in n):
            return True
    return False


async def gather_evidence(
    store,
    *,
    canonical_name: str,
    aliases: list[str],
    series_id: str,
    doc_id: str | None = None,
    max_dialogues: int = _MAX_DIALOGUE_SAMPLES,
    max_narratives: int = _MAX_NARRATIVE_SAMPLES,
) -> EvidencePack:
    """Pull dialogue/narrative evidence for one character from the index."""
    name_set = {canonical_name, *[a for a in aliases if a]}
    # Prefer exact speakers; drop ultra-short tokens
    name_set = {n for n in name_set if len(n) >= 2}

    pack = EvidencePack()
    try:
        from src.domain.novel.dialogue_local_llm import is_noise_speaker
    except Exception:
        def is_noise_speaker(name: str) -> bool:  # type: ignore
            return not name or name == "未知"

    # 1) Prefer scan of dialogue blocks (complete for speaker filter)
    blocks: list = []
    if hasattr(store, "iter_blocks"):
        blocks = list(
            store.iter_blocks(block_type="dialogue", doc_id=doc_id)
            if doc_id
            else store.iter_blocks(block_type="dialogue")
        )
        # If series spans multiple docs and no doc_id, optionally filter by roster doc_ids
        roster = load_roster(series_id)
        if roster and roster.doc_ids and not doc_id:
            allowed = set(roster.doc_ids)
            blocks = [b for b in blocks if getattr(b, "doc_id", "") in allowed]

    turns: list[dict] = []
    for block in blocks:
        gid = getattr(block, "global_id", "") or ""
        for turn in getattr(block, "dialogues", None) or []:
            sp = getattr(turn, "speaker", "") or ""
            if is_noise_speaker(sp):
                continue
            if not _speaker_match(sp, name_set):
                continue
            content = (getattr(turn, "content", "") or "").strip()
            if not content:
                continue
            turns.append(
                {
                    "speaker": sp,
                    "content": content,
                    "context": getattr(block, "chapter_title", "") or getattr(block, "scene", "") or "",
                    "global_id": gid,
                }
            )
            if gid and gid not in pack.sample_ids:
                pack.sample_ids.append(gid)

    pack.dialogue_hits = len(turns)
    # Diversify: keep order but cap
    pack.dialogues = turns[:max_dialogues]

    # 2) Narrative: vector search + light scan
    narratives: list[str] = []
    query = " ".join([canonical_name, *aliases[:3]])
    try:
        hits = await store.search(
            query,
            channel="narrative",
            doc_id=doc_id,
            top_k=max_narratives * 2,
        )
        for h in hits:
            text = (h.block.narrative_text or "").strip()
            if not text:
                continue
            if not any(n in text for n in name_set):
                continue
            # Short window around first mention
            idx = min(
                (text.find(n) for n in name_set if n in text),
                default=0,
            )
            snippet = text[max(0, idx - 40) : idx + 80].replace("\n", " ").strip()
            if snippet and snippet not in narratives:
                narratives.append(snippet)
            gid = getattr(h.block, "global_id", "") or ""
            if gid and gid not in pack.sample_ids:
                pack.sample_ids.append(gid)
            if len(narratives) >= max_narratives:
                break
    except Exception as e:
        logger.warning("Narrative gather failed for %s: %s", canonical_name, e)

    pack.narratives = narratives
    pack.narrative_hits = len(narratives)
    return pack


def _parse_json_object(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            # unwrap { "角色名": {...} } if single key
            if "traits" not in data and len(data) == 1:
                inner = next(iter(data.values()))
                if isinstance(inner, dict):
                    return inner
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


async def distill_single(
    llm_client,
    *,
    canonical_name: str,
    aliases: list[str],
    series_id: str,
    evidence: EvidencePack,
) -> dict[str, Any]:
    """One LLM call → single-character profile dict. Fallback heuristic if no LLM."""
    if llm_client is None:
        return _fallback_profile(canonical_name, evidence)

    alias_txt = "、".join(aliases[:8]) if aliases else canonical_name
    dlg_lines = []
    for i, d in enumerate(evidence.dialogues[:40], 1):
        dlg_lines.append(f"{i}. [{d.get('speaker')}] {d.get('content')}")
    nar_lines = [f"- {n}" for n in evidence.narratives[:15]]

    # 注入已有结构化关系（单一事实源 story_analysis 的投影）：
    # LLM 以之为先验修正/确认，而不是自由编造第二份关系清单
    # （避免卡片与检索/图谱的关系互相矛盾）。
    relation_block = ""
    try:
        from src.domain.novel.relation_store import (
            format_relations_block,
            relations_for_character,
        )

        existing = relations_for_character(series_id, canonical_name, aliases)
        if existing:
            relation_block = (
                "\n## 已有关系线索（来自剧情分析，供参考确认；如有出入请以证据修正，"
                "并保持与之一致）\n" + format_relations_block(existing)
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Relation injection failed for %s: %s", canonical_name, exc)

    user = (
        f"系列：{series_id}\n"
        f"目标角色正名：{canonical_name}\n"
        f"别名（均指同一人）：{alias_txt}\n\n"
        f"## 对话证据（{len(evidence.dialogues)} 条）\n"
        + ("\n".join(dlg_lines) or "（无）")
        + "\n\n## 叙事片段\n"
        + ("\n".join(nar_lines) or "（无）")
        + relation_block
        + "\n\n请输出该角色档案 JSON。"
    )

    t0 = time.perf_counter()
    try:
        raw = await llm_client.achat(
            messages=[
                {"role": "system", "content": _SINGLE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=4096,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as e:
        logger.warning("Distill LLM failed for %s: %s — fallback", canonical_name, e)
        return _fallback_profile(canonical_name, evidence)

    logger.info(
        "Single distill %s: %.1fs, out=%d",
        canonical_name,
        time.perf_counter() - t0,
        len(raw or ""),
    )
    data = _parse_json_object(raw or "")
    if data is None:
        # one repair attempt
        try:
            raw2 = await llm_client.achat(
                messages=[
                    {"role": "system", "content": _SINGLE_SYSTEM},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": raw or ""},
                    {"role": "user", "content": "上一段不是合法 JSON。请只输出符合 schema 的 JSON 对象。"},
                ],
                temperature=0.1,
                max_tokens=4096,
                extra_body={"thinking": {"type": "disabled"}},
            )
            data = _parse_json_object(raw2 or "")
        except Exception as e:
            logger.warning("Distill repair failed for %s: %s", canonical_name, e)
            data = None
    if data is None:
        logger.warning("Distill JSON failed for %s — using fallback", canonical_name)
        return _fallback_profile(canonical_name, evidence)
    return data


def _fallback_profile(canonical_name: str, evidence: EvidencePack) -> dict[str, Any]:
    try:
        from src.domain.novel.character_builder import CharacterBuilder
        builder = CharacterBuilder()
        pp = builder._fallback_personality_profile(
            canonical_name,
            narrative_snippets=evidence.narratives,
            dialogue_contents=[d.get("content", "") for d in evidence.dialogues],
        )
        return {
            "traits": dict(pp.traits) if pp and pp.traits else {},
            "speech": {},
            "catchphrases": list(pp.catchphrases) if pp else [],
            "emotional_tendencies": getattr(pp, "emotional_tendencies", "") or "",
            "role": "",
            "personality": builder._profile_to_legacy_personality(pp) if pp else "",
            "background": "（启发式生成，建议有 LLM 时重跑）",
            "relationships": "",
        }
    except Exception:
        return {
            "traits": {},
            "speech": {},
            "catchphrases": [],
            "personality": "",
            "background": "",
            "relationships": "",
        }


