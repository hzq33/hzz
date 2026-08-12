"""Speaker validation before dialogue quota indexing.

Implements Phase B of DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md:
vocative / addressee rejection, optional semi-hard candidate mapping.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_HONORIFIC = r"(大人|殿下|陛下|阁下|前辈|学姐|学长|同学|老师|先生|小姐|同志|ちゃん|さん|桑|君|様)"


def is_vocative_misattribute(speaker: str, content: str) -> bool:
    """True when content addresses ``speaker`` (呼格), so speaker is the callee.

    Example: speaker=维鲁多拉, content=\"维鲁多拉大人，您到底打算…\" → True.
    """
    sp = (speaker or "").strip()
    c = (content or "").strip()
    if not sp or sp == "未知" or not c:
        return False
    if len(sp) < 2:
        return False
    esc = re.escape(sp)
    # 「维鲁多拉大人，…」/「维鲁多拉！」/「维鲁多拉。」/「绫小路さん、…」
    if re.match(rf"^{esc}{_HONORIFIC}?[，,、！!？?…。．\s]", c):
        return True
    if re.match(rf"^{esc}{_HONORIFIC}$", c):
        return True
    # Bare name + pause particle common in CN light novels
    if re.match(rf"^{esc}[哟呀啊哇呐][，,！!？?\s]", c):
        return True
    return False


def map_to_candidate(
    speaker: str,
    candidates: Sequence[str],
) -> str | None:
    """Map speaker to a candidate canonical/alias if possible (substring soft match)."""
    sp = (speaker or "").strip()
    if not sp or sp == "未知":
        return None
    cands = [c.strip() for c in candidates if c and str(c).strip()]
    if not cands:
        return None
    if sp in cands:
        return sp
    for c in cands:
        if len(c) < 2:
            continue
        if sp.startswith(c) or c.startswith(sp) or c in sp or sp in c:
            return c
    return None


def sanitize_dialogue_turn(
    turn: dict[str, Any],
    *,
    candidates: Sequence[str] | None = None,
    accept_min_strict: float = 0.7,
    reject_vocative: bool = True,
) -> dict[str, Any]:
    """Return a copy of turn with speaker corrected or set to 未知.

    Adds optional ``_reject_reason`` for diagnostics (stripped before index if desired).
    """
    out = dict(turn)
    speaker = str(out.get("speaker") or "未知").strip() or "未知"
    content = str(out.get("content") or "").strip()
    conf = float(out.get("confidence") or 0.0)
    reason: str | None = None

    if reject_vocative and speaker != "未知" and is_vocative_misattribute(speaker, content):
        speaker = "未知"
        reason = "vocative"
        conf = min(conf, 0.3)

    cands = list(candidates or [])
    if speaker != "未知" and cands:
        mapped = map_to_candidate(speaker, cands)
        if mapped:
            speaker = mapped
        elif conf < float(accept_min_strict):
            speaker = "未知"
            reason = reason or "unmapped_low_conf"

    out["speaker"] = speaker
    out["confidence"] = conf
    if reason:
        out["_reject_reason"] = reason
    return out


def sanitize_turns(
    turns: Sequence[dict[str, Any]],
    *,
    candidates: Sequence[str] | None = None,
    accept_min_strict: float = 0.7,
    reject_vocative: bool = True,
) -> list[dict[str, Any]]:
    return [
        sanitize_dialogue_turn(
            t,
            candidates=candidates,
            accept_min_strict=accept_min_strict,
            reject_vocative=reject_vocative,
        )
        for t in turns
        if isinstance(t, dict)
    ]
