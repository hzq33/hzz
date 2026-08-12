"""Evaluate impersonation style-sample strategies on the live Lance index.

Compares:
  A) current: dialogue vector search(query=character+user) + mention filter
  B) character-filtered search + turn-level speaker keep
  C) card.sample_dialogues (cached)
  D) gather_evidence / scan pool curated samples

Run from repo root:
  python scripts/dev/eval_style_sample_strategies.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.application.novel.factory import create_novel_store
from src.application.novel.qa_expand import looks_like_fact_question
from src.domain.character_card import CharacterCard, _curate_dialogue_samples


CHARACTER = "利姆露"
ALIASES = ["利姆露", "利姆路", "利姆ル"]  # common variants if present
QUERIES = [
    "你好呀，利姆露先生",
    "你对库洛艾了解多少",
    "银发的小丫头不是黑发吗",
    "我想知道一些关于日向的事",
    "后来呢，后来你们关系怎么样",
]


def _turn_lines(hits) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for h in hits or []:
        for t in getattr(h.block, "dialogues", None) or []:
            out.append(((t.speaker or "").strip(), (t.content or "").strip()))
    return out


def _metrics(lines: list[tuple[str, str]], character: str, query: str) -> dict:
    if not lines:
        return {
            "n_lines": 0,
            "speaker_purity": 0.0,
            "char_in_line": 0.0,
            "query_entity_pollution": 0.0,
            "avg_len": 0.0,
        }
    # crude entity tokens from query (2+ CJK / alpha runs)
    import re

    ents = [m.group(0) for m in re.finditer(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", query)]
    ents = [e for e in ents if e not in {character, "了解", "多少", "后来", "关系", "怎么样", "不是", "黑发", "银发", "小丫头", "你好", "先生", "我想", "知道", "一些", "关于"}]
    n = len(lines)
    own = sum(1 for sp, _ in lines if character in sp or sp == character)
    mention = sum(1 for sp, ct in lines if character in sp or character in ct)
    polluted = 0
    if ents:
        polluted = sum(1 for _, ct in lines if any(e in ct for e in ents))
    avg_len = sum(len(ct) for _, ct in lines) / n
    return {
        "n_lines": n,
        "speaker_purity": round(own / n, 3),
        "char_in_line": round(mention / n, 3),
        "query_entity_pollution": round(polluted / n, 3) if ents else 0.0,
        "entities": ents,
        "avg_len": round(avg_len, 1),
        "preview": [f"[{sp}] {ct[:40]}" for sp, ct in lines[:4]],
    }


def _filter_mention(hits, character: str):
    kept = []
    for h in hits or []:
        block = h.block
        ok = character in (getattr(block, "scene", "") or "")
        for t in getattr(block, "dialogues", None) or []:
            if character in (t.speaker or "") or character in (t.content or ""):
                ok = True
                break
        if ok:
            kept.append(h)
    return kept


def _extract_character_turns(hits, name_set: set[str]) -> list[tuple[str, str]]:
    lines = []
    for h in hits or []:
        for t in getattr(h.block, "dialogues", None) or []:
            sp = (t.speaker or "").strip()
            ct = (t.content or "").strip()
            if not ct:
                continue
            if any(n == sp or n in sp for n in name_set):
                lines.append((sp, ct))
    return lines


async def main() -> None:
    store = create_novel_store()
    name_set = {n for n in [CHARACTER, *ALIASES] if len(n) >= 2}

    card = CharacterCard.load(CHARACTER)
    card_lines = []
    if card and card.sample_dialogues:
        card_lines = [
            (d.get("speaker") or CHARACTER, d.get("content") or "")
            for d in card.sample_dialogues
            if d.get("content")
        ]
    print("=== Card samples ===")
    print(json.dumps(_metrics(card_lines, CHARACTER, ""), ensure_ascii=False, indent=2))
    if card_lines:
        for sp, ct in card_lines[:8]:
            print(f"  [{sp}] {ct}")

    # Pool via gather_evidence if possible
    pool_lines: list[tuple[str, str]] = []
    try:
        from src.domain.novel.character_on_demand import gather_evidence

        # series_id best-effort from first narrative hit
        series_id = ""
        narr = await store.search(CHARACTER, channel="narrative", top_k=1)
        if narr:
            series_id = (narr[0].block.doc_id or "").split("__")[0]
        pack = await gather_evidence(
            store,
            canonical_name=CHARACTER,
            aliases=list(name_set),
            series_id=series_id or "unknown",
            max_dialogues=200,
        )
        for t in pack.dialogues or []:
            pool_lines.append((t.get("speaker") or "", t.get("content") or ""))
        curated = _curate_dialogue_samples(list(pack.dialogues or []), CHARACTER, max_n=8)
        curated_lines = [(d["speaker"], d["content"]) for d in curated]
    except Exception as e:
        print("gather_evidence failed:", e)
        curated_lines = []
        pack = None

    print("\n=== Pool / curated from extracted turns ===")
    print("pool_size", len(pool_lines))
    print("curated", json.dumps(_metrics(curated_lines, CHARACTER, ""), ensure_ascii=False, indent=2))
    for sp, ct in curated_lines[:8]:
        print(f"  [{sp}] {ct}")

    rows = []
    for q in QUERIES:
        print("\n" + "=" * 72)
        print("Q:", q, "| fact?", looks_like_fact_question(q))

        # A current
        hits_a = await store.search(
            f"{CHARACTER} {q}", channel="dialogue", top_k=3
        )
        hits_a = _filter_mention(hits_a, CHARACTER)
        lines_a = _turn_lines(hits_a)

        # B filtered search + speaker turns only
        hits_b = await store.search(
            q,
            channel="dialogue",
            top_k=8,
            filters={"characters": [CHARACTER]},
        )
        lines_b = _extract_character_turns(hits_b, name_set)[:6]

        # C card (static)
        lines_c = card_lines[:5]

        # D curated pool (static / intent-agnostic)
        lines_d = curated_lines[:5]

        ma, mb, mc, md = (
            _metrics(lines_a, CHARACTER, q),
            _metrics(lines_b, CHARACTER, q),
            _metrics(lines_c, CHARACTER, q),
            _metrics(lines_d, CHARACTER, q),
        )
        print("A current blocks→lines", ma)
        print("B char-filter+speaker", mb)
        print("C card", mc)
        print("D curated pool", md)
        rows.append(
            {
                "query": q,
                "fact": looks_like_fact_question(q),
                "A": ma,
                "B": mb,
                "C": mc,
                "D": md,
            }
        )

    # Summary
    def avg(key_path):
        vals = []
        for r in rows:
            for strat in ("A", "B", "C", "D"):
                pass
        out = {}
        for strat in ("A", "B", "C", "D"):
            xs = [r[strat][key_path] for r in rows]
            out[strat] = round(sum(xs) / len(xs), 3) if xs else 0
        return out

    print("\n=== MEAN across queries ===")
    print("speaker_purity", avg("speaker_purity"))
    print("query_entity_pollution", avg("query_entity_pollution"))
    print("n_lines", avg("n_lines"))

    out_path = ROOT / "scripts" / "dev" / "_style_eval_out.json"
    out_path.write_text(
        json.dumps(
            {
                "character": CHARACTER,
                "card_n": len(card_lines),
                "pool_n": len(pool_lines),
                "curated_n": len(curated_lines),
                "rows": rows,
                "mean_speaker_purity": avg("speaker_purity"),
                "mean_pollution": avg("query_entity_pollution"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote", out_path)


if __name__ == "__main__":
    asyncio.run(main())
