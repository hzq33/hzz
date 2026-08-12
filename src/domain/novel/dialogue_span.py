"""Dialogue span extraction — quote-first, offset-aware (Stage A/B).

Used by short-window speaker attribution and ingest dialogue pipeline.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# Paired quote styles: (open, close)
_QUOTE_PAIRS = (
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
    ('"', '"'),
)

_NOISE = {
    "未知", "他", "她", "它", "我", "你", "他们", "她们", "众人", "大家",
    "三人", "两人", "四人", "少年", "少女", "男人", "女人", "有人", "谁",
    "那人", "此人", "自己", "什么", "怎么", "可以", "不是", "已经",
    "还是", "这个", "那个", "因为", "所以", "然后", "开始", "现在",
    "也就是", "不如", "不知", "顺便", "急忙", "能不能", "虽然",
    "重重地", "温柔地", "狠狠地", "轻轻地", "慢慢地", "突然", "忽然",
    "终于", "随即", "渐渐", "转身", "轻小", "隔壁",
}

# Function-word / adverb debris often mistaken for names
_NOISE_SUFFIX = ("地", "的", "着", "了", "过", "吗", "呢", "吧", "啊")

# Name：「quote」 on same line (prefix before open quote)
_PREFIX_NAME = re.compile(
    r"(?:^|[^\u4e00-\u9fff])([\u4e00-\u9fff]{2,8})"
    r"(?:同学|小姐|先生)?"
    r"(?:\s*(?:说|道|问|答|喊|叫|笑道|冷笑道))?"
    r"[：:]\s*$"
)

# 「quote」——Name / 「quote」Name说
_POSTFIX_NAME = re.compile(
    r"^\s*[—\-–]{1,2}\s*([\u4e00-\u9fff]{2,8})"
    r"(?:同学|小姐|先生)?"
    r"(?:\s*(?:说|道|问|答|喊|叫))?"
    r"(?=\s|$|[。！？])"
)
_POSTFIX_NAME_SOFT = re.compile(
    r"^\s*([\u4e00-\u9fff]{2,8})"
    r"(?:同学|小姐|先生)?"
    r"(?:说|道|问|答|喊|叫)"
)

# Narrative subject before quote: Name + verb
_CONTEXT_NAME = re.compile(
    r"(?:^|[^\u4e00-\u9fff])([\u4e00-\u9fff]{2,3})"
    r"(?:同学|小姐|先生)?"
    r"(?:咬|抬|站|坐|低|看|说|道|哭|笑|叹|转|垂|望|走|跑|停|问|答|叫|喊|冲|瞪"
    r"|踩|拍|拉|推|抱|踢|跳|甩|挽|抚|抹|擦|捂|晃|点|握|皱)"
)


@dataclass
class DialogueSpan:
    span_id: str
    content: str
    start: int
    end: int
    quote_style: str
    speaker_hint: str = "未知"
    hint_source: str = "none"
    confidence: float = 0.0
    needs_attribution: bool = True
    line_no: int | None = None


@dataclass
class SpanExtractResult:
    spans: list[DialogueSpan] = field(default_factory=list)
    text: str = ""


def is_noise_speaker(name: str) -> bool:
    s = (name or "").strip()
    if not s or s in _NOISE:
        return True
    if len(s) == 1:
        return True
    if len(s) <= 2 and s[-1] in "说道路笑走看叹问喊":
        return True
    if s[-1] in _NOISE_SUFFIX and len(s) <= 4:
        return True
    # Pronoun-led debris: 我知、你是、她一边、某人曾
    if s.startswith(("我", "你", "他", "她", "这", "那", "某")) and len(s) <= 4:
        return True
    if s.startswith(("也", "还", "就", "又", "都")) and len(s) <= 4:
        if any(ch in s for ch in "知是在不也就还又都某怎所"):
            return True
    return False


def extract_spans(
    text: str,
    *,
    high_confidence_min: float = 0.85,
    review_context_infer: bool = True,
    speaker_mode: str = "none",
) -> SpanExtractResult:
    """Stage A(+optional B): find quote spans.

    Args:
        speaker_mode:
          - ``none``: content recall only; speaker=未知, all need attribution
          - ``high_only``: only named_colon / postfix_said; no context_infer / inherit
          - ``rules``: legacy rule hints (named_colon / context_infer / inherit)
    """
    spans: list[DialogueSpan] = []
    occupied: list[tuple[int, int]] = []
    use_rules = speaker_mode in ("rules", "high_only")
    high_only = speaker_mode == "high_only"

    for open_q, close_q in _QUOTE_PAIRS:
        for start, end, content in _iter_quotes(text, open_q, close_q):
            if _overlaps(start, end, occupied):
                continue
            stripped = content.strip()
            if len(stripped) < 1:
                continue
            # Drop pure punctuation / tiny debris spans
            if len(stripped) < 2 and not re.search(r"[\u4e00-\u9fff]", stripped):
                continue
            occupied.append((start, end))
            line_no = text.count("\n", 0, start) + 1

            if use_rules:
                hint, source, conf = _infer_hint(
                    text, start, end, content, allow_context_infer=not high_only
                )
                if is_noise_speaker(hint):
                    hint, source, conf = (
                        "未知",
                        "none" if source == "none" else source,
                        min(conf, 0.3),
                    )
                    if source not in ("none",):
                        source = "noise_" + source
                needs = conf < high_confidence_min or is_noise_speaker(hint)
                if source == "context_infer" and review_context_infer:
                    needs = True
                if source.startswith("noise_"):
                    needs = True
            else:
                hint, source, conf, needs = "未知", "none", 0.0, True

            spans.append(
                DialogueSpan(
                    span_id=f"{line_no}:{start}:{end}",
                    content=stripped,
                    start=start,
                    end=end,
                    quote_style=f"{open_q}{close_q}",
                    speaker_hint=hint if hint else "未知",
                    hint_source=source,
                    confidence=float(conf),
                    needs_attribution=needs,
                    line_no=line_no,
                )
            )

    spans.sort(key=lambda s: s.start)

    if speaker_mode == "rules":
        last_reliable = "未知"
        for sp in spans:
            if sp.hint_source in ("named_colon", "postfix_said") and not is_noise_speaker(
                sp.speaker_hint
            ):
                last_reliable = sp.speaker_hint
            elif sp.hint_source == "none" and last_reliable != "未知":
                sp.speaker_hint = last_reliable
                sp.hint_source = "inherit"
                sp.confidence = 0.45
                sp.needs_attribution = True
            if (
                not sp.needs_attribution
                and not is_noise_speaker(sp.speaker_hint)
                and sp.confidence >= high_confidence_min
            ):
                last_reliable = sp.speaker_hint

    # high_only: ensure non-explicit sources always need attribution
    if high_only:
        for sp in spans:
            if sp.hint_source not in ("named_colon", "postfix_said") or is_noise_speaker(sp.speaker_hint) or sp.confidence < high_confidence_min:
                sp.speaker_hint = "未知"
                sp.hint_source = "none"
                sp.confidence = 0.0
                sp.needs_attribution = True
            else:
                sp.needs_attribution = False

    return SpanExtractResult(spans=spans, text=text)


def _iter_quotes(text: str, open_q: str, close_q: str) -> Iterable[tuple[int, int, str]]:
    i = 0
    n = len(text)
    same = open_q == close_q
    while i < n:
        if text[i] != open_q:
            i += 1
            continue
        j = i + 1
        while j < n and text[j] != close_q:
            # For ASCII ", skip escaped \"
            if same and text[j] == "\\" and j + 1 < n:
                j += 2
                continue
            j += 1
        if j >= n:
            break
        content = text[i + 1 : j]
        # Skip empty / narrative-looking mega quotes (>200 chars often narration abuse)
        if content.strip() and len(content) <= 200:
            yield i, j + 1, content
        i = j + 1


def _overlaps(start: int, end: int, occupied: Sequence[tuple[int, int]]) -> bool:
    for a, b in occupied:
        if start < b and end > a:
            return True
    return False


def _infer_hint(
    text: str,
    start: int,
    end: int,
    content: str,
    *,
    allow_context_infer: bool = True,
) -> tuple[str, str, float]:
    # Prefix on same line before open quote
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    m = _PREFIX_NAME.search(prefix)
    if m:
        name = m.group(1).strip()
        if not is_noise_speaker(name):
            return name, "named_colon", 0.92

    # Postfix after close quote on same line
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    suffix = text[end:line_end]
    m = _POSTFIX_NAME.match(suffix) or _POSTFIX_NAME_SOFT.match(suffix)
    if m:
        name = m.group(1).strip()
        if not is_noise_speaker(name):
            return name, "postfix_said", 0.88

    if not allow_context_infer:
        return "未知", "none", 0.0

    # Narrative context: last 1–2 sentences before quote
    before = text[max(0, start - 120) : start]
    sentences = re.split(r"[。！？\n]", before)
    for s in reversed(sentences[-3:]):
        s = s.strip()
        if len(s) < 4:
            continue
        cm = _CONTEXT_NAME.search(s)
        if cm:
            name = cm.group(1).strip()
            if not is_noise_speaker(name):
                return name, "context_infer", 0.62

    return "未知", "none", 0.0
