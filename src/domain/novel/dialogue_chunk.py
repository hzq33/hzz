"""Chapter filtering + char-budget windows for dialogue LLM extraction.

Implements DIALOGUE_CHAPTER_EXTRACT_DESIGN.md:
  F0 skip intro/afterword / no-quote chapters
  F1 whole-chapter or in-chapter sliding windows
  F3 overlap dedupe of extracted turns
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# Default title blacklist (简介/后记/制作信息等)
DEFAULT_SKIP_TITLE_RE = re.compile(
    r"(简介|制作信息|版权|后记|译后记|作者的话|人物介绍|目录|插图|彩页|广告|"
    r"封面|奥付|colophon|copyright|afterword|foreword|credits)",
    re.IGNORECASE,
)

# Default: CJK corner quotes + CN/EN curly/straight double quotes
DEFAULT_QUOTE_RE = re.compile(r"[「」『』“”\"]")
_QUOTE_RE = DEFAULT_QUOTE_RE


def _quote_pattern(quote_patterns: str | None = None) -> re.Pattern[str]:
    raw = (quote_patterns or "").strip()
    if not raw:
        return DEFAULT_QUOTE_RE
    try:
        return re.compile(raw)
    except re.error:
        return DEFAULT_QUOTE_RE


@dataclass
class TextWindow:
    """One LLM input slice (usually within a single chapter)."""

    chapter_index: int
    chapter_title: str
    text: str
    start: int  # offset in chapter.text
    end: int
    window_index: int
    slid: bool = False


@dataclass
class SkipInfo:
    chapter_index: int
    title: str
    reason: str


@dataclass
class DedupeResult:
    turns: list[dict] = field(default_factory=list)
    dropped: int = 0
    conflicts: int = 0


def count_quote_marks(text: str, *, quote_patterns: str | None = None) -> int:
    return len(_quote_pattern(quote_patterns).findall(text or ""))


def should_skip_chapter(
    title: str,
    text: str,
    *,
    skip_title_re: re.Pattern[str] | None = None,
    require_quote_marks: bool = True,
    min_chapter_chars: int = 80,
    quote_patterns: str | None = None,
) -> str | None:
    """Return skip reason, or None if chapter should be processed."""
    title = (title or "").strip()
    text = text or ""
    if not text.strip():
        return "empty"
    quotes = count_quote_marks(text, quote_patterns=quote_patterns)
    pat = skip_title_re or DEFAULT_SKIP_TITLE_RE
    if title and pat.search(title):
        # Still skip, but separate reason when body has quotes (mis-kill diagnostics)
        return "title_blacklist_has_quotes" if quotes > 0 else "title_blacklist"
    if require_quote_marks and quotes == 0:
        if len(text.strip()) < max(1, int(min_chapter_chars)):
            return "too_short_no_quotes"
        return "no_quotes"
    return None


def iter_chapter_windows(
    chapter_index: int,
    chapter_title: str,
    text: str,
    *,
    max_chunk_chars: int = 6000,
    slide_win_chars: int = 3500,
    slide_stride_chars: int = 2000,
) -> list[TextWindow]:
    """Whole chapter if short; else sliding windows inside the chapter only."""
    text = text or ""
    if not text.strip():
        return []
    max_chunk = max(500, int(max_chunk_chars))
    win = max(500, int(slide_win_chars))
    stride = max(200, int(slide_stride_chars))
    if stride > win:
        stride = win

    n = len(text)
    if n <= max_chunk:
        return [
            TextWindow(
                chapter_index=chapter_index,
                chapter_title=chapter_title or "",
                text=text,
                start=0,
                end=n,
                window_index=0,
                slid=False,
            )
        ]

    windows: list[TextWindow] = []
    pos = 0
    wi = 0
    while pos < n:
        end = min(n, pos + win)
        windows.append(
            TextWindow(
                chapter_index=chapter_index,
                chapter_title=chapter_title or "",
                text=text[pos:end],
                start=pos,
                end=end,
                window_index=wi,
                slid=True,
            )
        )
        wi += 1
        if end >= n:
            break
        pos += stride
    return windows


def normalize_dialogue_content(content: str) -> str:
    s = (content or "").strip()
    s = s.strip("「」『』\"“”'‘’")
    s = re.sub(r"\s+", "", s)
    return s


def _speaker_compatible(a: str, b: str) -> bool:
    a = (a or "").strip() or "未知"
    b = (b or "").strip() or "未知"
    if a == b:
        return True
    if a == "未知" or b == "未知":
        return True
    return False


def dedupe_turns(
    turns: Sequence[dict[str, Any]],
) -> DedupeResult:
    """Drop overlap duplicates; on speaker conflict keep higher confidence."""
    kept: list[dict] = []
    # key -> index in kept
    index_by_content: dict[str, int] = {}
    dropped = 0
    conflicts = 0

    for raw in turns:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        # Skip silence placeholders
        if re.fullmatch(r"[—\-–−･・….\s]+", content):
            dropped += 1
            continue
        key = normalize_dialogue_content(content)
        if not key:
            dropped += 1
            continue
        speaker = str(raw.get("speaker") or "未知").strip() or "未知"
        conf = float(raw.get("confidence") or 0.0)
        item = {
            "speaker": speaker,
            "content": content,
            "confidence": conf,
        }
        if key in index_by_content:
            prev_i = index_by_content[key]
            prev = kept[prev_i]
            if _speaker_compatible(prev["speaker"], speaker):
                # Prefer named speaker over 未知; else higher conf
                if prev["speaker"] == "未知" and speaker != "未知" or speaker != "未知" and conf > float(prev.get("confidence") or 0):
                    kept[prev_i] = item
                dropped += 1
                continue
            # Conflict: keep higher confidence
            conflicts += 1
            if conf > float(prev.get("confidence") or 0):
                kept[prev_i] = item
            dropped += 1
            continue
        index_by_content[key] = len(kept)
        kept.append(item)

    return DedupeResult(turns=kept, dropped=dropped, conflicts=conflicts)


def plan_document_windows(
    chapters: Sequence[Any],
    *,
    max_chunk_chars: int = 6000,
    slide_win_chars: int = 3500,
    slide_stride_chars: int = 2000,
    require_quote_marks: bool = True,
    min_chapter_chars: int = 80,
    skip_title_patterns: str | None = None,
    quote_patterns: str | None = None,
) -> tuple[list[TextWindow], list[SkipInfo], dict]:
    """Filter chapters and build windows for a whole document."""
    skip_re = (
        re.compile(skip_title_patterns, re.IGNORECASE)
        if skip_title_patterns
        else DEFAULT_SKIP_TITLE_RE
    )
    windows: list[TextWindow] = []
    skipped: list[SkipInfo] = []
    slide_chapters = 0
    chapters_total = 0

    for i, ch in enumerate(chapters or []):
        chapters_total += 1
        title = getattr(ch, "title", None) or ""
        text = getattr(ch, "text", None) or ""
        reason = should_skip_chapter(
            title,
            text,
            skip_title_re=skip_re,
            require_quote_marks=require_quote_marks,
            min_chapter_chars=min_chapter_chars,
            quote_patterns=quote_patterns,
        )
        if reason:
            skipped.append(SkipInfo(chapter_index=i, title=title, reason=reason))
            continue
        wins = iter_chapter_windows(
            i,
            title,
            text,
            max_chunk_chars=max_chunk_chars,
            slide_win_chars=slide_win_chars,
            slide_stride_chars=slide_stride_chars,
        )
        if wins and wins[0].slid:
            slide_chapters += 1
        windows.extend(wins)

    skip_reasons: dict[str, int] = {}
    for s in skipped:
        skip_reasons[s.reason] = skip_reasons.get(s.reason, 0) + 1
    meta = {
        "chapters_total": chapters_total,
        "chapters_skipped": len(skipped),
        "skip_reasons": skip_reasons,
        "windows": len(windows),
        "slide_chapters": slide_chapters,
    }
    return windows, skipped, meta
