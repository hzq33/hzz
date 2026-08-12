"""Parse volume / chapter hints from free-text novel queries."""

from __future__ import annotations

import re
from collections.abc import Sequence

_CN_NUM = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _cn_to_int(token: str) -> int | None:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CN_NUM:
        return _CN_NUM[token]
    if token.startswith("十"):
        if len(token) == 1:
            return 10
        if len(token) == 2 and token[1] in _CN_NUM:
            return 10 + _CN_NUM[token[1]]
    if len(token) == 2 and token[0] in _CN_NUM and token[1] == "十":
        return _CN_NUM[token[0]] * 10
    if (
        len(token) == 3
        and token[0] in _CN_NUM
        and token[1] == "十"
        and token[2] in _CN_NUM
    ):
        return _CN_NUM[token[0]] * 10 + _CN_NUM[token[2]]
    return None


def parse_volume_hint(query: str) -> int | None:
    """Extract 1-based volume number from query text, if present."""
    text = query or ""
    m = re.search(
        r"(?:第\s*([一二三四五六七八九十百零〇两\d]+)\s*卷|Vol\.?\s*(\d+)|__vol0*(\d+))",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    token = next((g for g in m.groups() if g), None)
    if not token:
        return None
    if token.isdigit():
        n = int(token)
        return n if 1 <= n <= 99 else None
    return _cn_to_int(token)


def parse_section_hint(query: str) -> tuple[str, int] | None:
    """Return (unit, n) for 第N章/节/回/話 if present."""
    text = query or ""
    m = re.search(
        r"第\s*([一二三四五六七八九十百零〇两\d]+)\s*([章节節回話话])",
        text,
    )
    if not m:
        return None
    n = _cn_to_int(m.group(1))
    if n is None or n < 1:
        return None
    unit = m.group(2)
    if unit in {"节", "節"}:
        unit = "节"
    elif unit in {"章"}:
        unit = "章"
    elif unit in {"回"}:
        unit = "回"
    else:
        unit = "话"
    return unit, n


def chapter_match_keys(unit: str, n: int) -> list[str]:
    """Candidate substrings for chapter_title contains matching."""
    arabic = str(n)
    # Build simple CN for 1..20
    cn_simple = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十",
        11: "十一",
        12: "十二",
        13: "十三",
        14: "十四",
        15: "十五",
        16: "十六",
        17: "十七",
        18: "十八",
        19: "十九",
        20: "二十",
    }
    cn = cn_simple.get(n, arabic)
    units = [unit]
    if unit == "节":
        units.extend(["節", "章"])
    elif unit == "章":
        units.append("节")
    keys: list[str] = []
    for u in units:
        keys.extend(
            [
                f"第{arabic}{u}",
                f"第{cn}{u}",
                f"{arabic}{u}",
                f"{cn}{u}",
            ]
        )
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def resolve_doc_id_for_volume(
    doc_ids: Sequence[str],
    volume_no: int,
    *,
    series_hint: str = "",
) -> str | None:
    """Pick ``...__volNN`` from indexed doc_ids."""
    suffix = f"__vol{int(volume_no):02d}"
    candidates = [d for d in doc_ids if str(d).endswith(suffix)]
    if not candidates:
        # also accept unpadded
        suffix2 = f"__vol{int(volume_no)}"
        candidates = [d for d in doc_ids if str(d).endswith(suffix2)]
    if not candidates:
        return None
    hint = (series_hint or "").strip()
    if hint:
        for d in candidates:
            if hint in d:
                return d
    return sorted(candidates)[0]


def series_id_from_doc_id(doc_id: str) -> str:
    return re.sub(r"__vol\d+$", "", (doc_id or "").strip(), flags=re.IGNORECASE)


def list_ordered_chapter_titles(doc_id: str) -> list[str]:
    """Chapter titles for a volume in catalog order (1st = index 0)."""
    sid = series_id_from_doc_id(doc_id)
    if not sid:
        return []
    try:
        from src.domain.novel.catalog import load_catalog

        catalog = load_catalog(sid)
    except Exception:
        return []
    if not catalog:
        return []
    volume = next((v for v in catalog.volumes if v.doc_id == doc_id), None)
    if volume is None:
        # fallback: match by volume_no suffix
        m = re.search(r"__vol0*(\d+)$", doc_id, flags=re.IGNORECASE)
        if m:
            vol_no = int(m.group(1))
            volume = next(
                (v for v in catalog.volumes if v.volume_no == vol_no),
                None,
            )
    if not volume:
        return []
    chapters = sorted(volume.chapters or [], key=lambda c: (c.order, c.title))
    return [c.title.strip() for c in chapters if (c.title or "").strip()]


def resolve_chapter_by_ordinal(doc_id: str, n: int) -> tuple[str | None, list[str]]:
    """Map 1-based「第N节/章」to real chapter title via catalog order.

    Returns (title_or_none, all_titles_in_order).
    """
    titles = list_ordered_chapter_titles(doc_id)
    if not titles:
        return None, []
    if n < 1 or n > len(titles):
        return None, titles
    return titles[n - 1], titles


def is_toc_intent(query: str) -> bool:
    """True when the user wants a chapter directory, not semantic search."""
    text = (query or "").strip()
    if not text:
        return False
    # Avoid treating "第N节内容" as TOC
    if parse_section_hint(text) and not re.search(
        r"目录|章[节節]列表|各[章节節]标题|列出.{0,6}章", text
    ):
        return False
    return bool(
        re.search(
            r"(章节目录|目录|章[节節]列表|各[章节節]标题|列出.{0,8}章[节節名]?|"
            r"有哪些章|章名列表)",
            text,
        )
    )
