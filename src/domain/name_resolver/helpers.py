"""Name resolution helpers — simplified conversion, honorific strip, distance.

Extracted from the former monolithic ``name_resolver.py``; logic unchanged.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("agent")


# ── Stage 1: 繁简统一 ─────────────────────────────────────

try:
    from opencc import OpenCC

    _cc = OpenCC("t2s")  # Traditional → Simplified

    def _to_simplified(text: str) -> str:
        """Convert Traditional Chinese to Simplified."""
        return _cc.convert(text)

    _HAS_OPENCC = True
except ImportError:
    _HAS_OPENCC = False

    def _to_simplified(text: str) -> str:
        """No-op fallback when opencc is not installed."""
        return text


# ── Stage 2: 称谓剥离 ─────────────────────────────────────

# Chinese honorific suffixes (longer patterns first for correct matching)
_CN_HONORIFICS: list[str] = [
    # Multi-char
    "大小姐", "大少爷", "二少爷", "二小姐", "三少爷", "三小姐",
    "老先生", "老夫人",
    "大小姐", "大公子",
    # Two-char
    "小姐", "少爷", "公子", "先生", "夫人", "太太",
    "师兄", "师姐", "师弟", "师妹",
    "师尊", "师父", "师傅",
    "前辈", "晚辈",
    "殿下", "大人", "陛下", "王爷",
    "同学", "学弟", "学妹", "学长",
    "姐姐", "哥哥", "弟弟", "妹妹",
    "大叔", "大伯", "大爷", "大妈",
    "阿姨", "叔叔",
    # Single-char (less aggressive, only strip if name remains ≥2 chars)
    "姐", "哥", "弟", "妹", "爷", "叔", "伯",
]

# Japanese honorific suffixes (from dialogue.py, extended)
_JP_HONORIFICS: list[str] = [
    "さん", "くん", "ちゃん", "さま", "様",
    "先輩", "先生", "氏",
    "殿下", "大人",
]

# Compile regex: match any honorific at end of string
_HONORIFIC_PATTERN = re.compile(
    r"(" + "|".join(re.escape(h) for h in sorted(_CN_HONORIFICS + _JP_HONORIFICS, key=len, reverse=True)) + r")$"
)

# Non-name words that should be filtered entirely (from character_builder.py)
_NON_NAME_WORDS: set[str] = {
    "未知", "他", "她", "它", "我", "你", "他们", "她们", "众人", "大家",
    "俺", "僕", "私", "あたし", "わたし", "彼", "彼女", "あいつ", "こいつ",
    "终于", "随即", "忽然", "突然", "渐渐", "慢慢", "轻轻", "微微",
    "冷冷", "认真", "相视", "一丝",
    "姑娘", "公子", "少爷", "小姐", "师父", "师傅", "弟子",
    "父亲", "母亲", "前辈", "晚辈",
    "少年", "少女", "中年人", "年轻人",
    "四周", "身边", "后面", "前面", "里面", "外面", "旁边",
    "时候", "不知", "两人", "一人", "三人", "几人", "数人",
    "冷笑", "笑道", "点头", "摇头", "转身", "抬头", "低头",
    "胡说", "胡说八", "胡说八道",
    "两人齐声", "众人齐声", "齐声",
    "我听", "我问", "我看", "你听", "你说", "他就",
    "却被", "也被", "还将", "却又",
    "那人", "此人", "有人",
}




def _strip_honorific(name: str) -> str:
    """Strip honorific suffix from a name.

    Returns the stripped result even if it's a single char (likely a surname).
    The caller decides how to handle short results.

    Examples:
        林姐姐 → 林 (surname only, but returned for prefix matching)
        晚晴姐 → 晚晴
        顾清寒少爷 → 顾清寒
        八奈見さん → 八奈見
    """
    result = _HONORIFIC_PATTERN.sub("", name.strip()).strip()
    return result if result else name


# ── Stage 3: 编辑距离 ─────────────────────────────────────


def _is_structural_match(name_a: str, name_b: str) -> bool:
    """Check if two names have a structural similarity (substring/prefix).

    Used by Stage 4 to exempt structurally-similar pairs from co-occurrence
    splitting — they're the same person referred to differently.
    """
    if name_a == name_b:
        return True
    # Substring
    if len(name_a) >= 2 and len(name_b) >= 2:
        if name_a in name_b or name_b in name_a:
            return True
    # Prefix (single-char surname as prefix of full name)
    short, long_ = (name_a, name_b) if len(name_a) <= len(name_b) else (name_b, name_a)
    if len(short) == 1 and len(long_) >= 2 and long_.startswith(short):
        return True
    return False


def _edit_distance(s1: str, s2: str) -> int:
    """Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _is_substring_or_similar(name_a: str, name_b: str, max_distance: int = 1) -> bool:
    """Check if two names are likely the same character.

    Rules:
    1. One is substring of the other (林晚晴 contains 晚晴)
    2. One is prefix of the other (林 is prefix of 林晚晴 - surname match)
    3. Edit distance <= max_distance - BUT NOT for two different single chars
       (林 and 顾 have edit_distance=1 but are different surnames!)
    """
    if name_a == name_b:
        return True

    # Substring check: shorter name is part of longer name (both ≥2 chars)
    if len(name_a) >= 2 and len(name_b) >= 2:
        if name_a in name_b or name_b in name_a:
            return True

    # Prefix check: 1-char name is prefix of a longer name (≥2 chars)
    # This catches "林" (surname) as prefix of "林晚" or "林晚晴"
    short, long_ = (name_a, name_b) if len(name_a) <= len(name_b) else (name_b, name_a)
    if len(short) == 1 and len(long_) >= 2 and long_.startswith(short):
        return True

    # Edit distance check
    dist = _edit_distance(name_a, name_b)
    if dist <= max_distance:
        # CRITICAL: Two different single chars must NOT merge via edit distance
        # (林 and 顾 have distance 1 but are different surnames)
        if len(name_a) == 1 and len(name_b) == 1:
            return False

        # For 2+char names, only merge if they share a common prefix
        if len(name_a) <= 3 or len(name_b) <= 3:
            # Still require at least 1 char in common for 2-char names
            if len(name_a) == 2 and len(name_b) == 2:
                return name_a[0] == name_b[0] or name_a[1] == name_b[1]
            return True
        # For longer names, require shared prefix of ≥2 chars
        if name_a[:2] == name_b[:2]:
            return True

    return False


# ── NameResolver ──────────────────────────────────────────


