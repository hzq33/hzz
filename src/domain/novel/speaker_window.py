"""Short-window builder for speaker attribution (Stage C)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.domain.novel.dialogue_span import DialogueSpan, is_noise_speaker


@dataclass
class AttributionWindow:
    window_id: str
    target_span_ids: list[str]
    target_contents: list[str]
    context_before: str
    context_after: str
    neighbor_dialogues: list[tuple[str, str]] = field(default_factory=list)
    candidate_speakers: list[str] = field(default_factory=list)
    chapter_title: str = ""
    alias_map_text: str = ""


_SENT_SPLIT = re.compile(r"(?<=[。！？\n])")


def build_windows(
    text: str,
    spans: Sequence[DialogueSpan],
    *,
    batch_size: int = 1,
    context_sentences: int = 2,
    max_candidates: int = 12,
    chapter_title: str = "",
    extra_candidates: Sequence[str] | None = None,
) -> list[AttributionWindow]:
    """Build short windows for spans that need attribution."""
    need = [s for s in spans if s.needs_attribution]
    if not need:
        return []

    # High-confidence anchors for neighbors / candidates
    reliable = [
        s
        for s in spans
        if not s.needs_attribution and not is_noise_speaker(s.speaker_hint)
    ]
    base_cands = _collect_candidates(spans, extra_candidates, max_candidates)

    windows: list[AttributionWindow] = []
    i = 0
    while i < len(need):
        group = need[i : i + max(1, batch_size)]
        # Same-paragraph preference: stop batch if gap > 400 chars
        trimmed = [group[0]]
        for sp in group[1:]:
            if sp.start - trimmed[-1].end > 400:
                break
            trimmed.append(sp)
        group = trimmed
        i += len(group)

        first, last = group[0], group[-1]
        before = _take_sentences(text, first.start, context_sentences, backward=True)
        after = _take_sentences(text, last.end, context_sentences, backward=False)
        neighbors = _neighbors(spans, first, last, reliable)

        cands = list(base_cands)
        for sp in group:
            if sp.speaker_hint and sp.speaker_hint not in cands and not is_noise_speaker(
                sp.speaker_hint
            ):
                cands.insert(0, sp.speaker_hint)
        for sp_name, _ in neighbors:
            if sp_name not in cands and not is_noise_speaker(sp_name):
                cands.insert(0, sp_name)
        if "未知" not in cands:
            cands.append("未知")
        cands = cands[:max_candidates]
        if "未知" not in cands:
            cands.append("未知")

        windows.append(
            AttributionWindow(
                window_id=f"w_{first.span_id}",
                target_span_ids=[s.span_id for s in group],
                target_contents=[s.content for s in group],
                context_before=before,
                context_after=after,
                neighbor_dialogues=neighbors,
                candidate_speakers=cands,
                chapter_title=chapter_title,
            )
        )
    return windows


def format_window_prompt(w: AttributionWindow) -> str:
    lines = []
    if w.chapter_title:
        lines.append(f"【章节】{w.chapter_title}")
    lines.append("【候选说话人】" + ", ".join(w.candidate_speakers))
    if w.alias_map_text:
        lines.append(f"【角色全名映射表（别名→全名，归因时用全名）】{w.alias_map_text}")
    if w.context_before.strip():
        lines.append("【前文】" + w.context_before.strip())
    lines.append("【待判定对话】")
    for sid, content in zip(w.target_span_ids, w.target_contents):
        lines.append(f'  - span_id={sid} content「{content}」')
    if w.context_after.strip():
        lines.append("【后文】" + w.context_after.strip())
    if w.neighbor_dialogues:
        lines.append("【邻近已确认对话】")
        for sp, ct in w.neighbor_dialogues:
            lines.append(f"  - {sp}: 「{ct[:40]}」")
    return "\n".join(lines)


def _take_sentences(text: str, pos: int, n: int, *, backward: bool) -> str:
    if n <= 0:
        return ""
    if backward:
        chunk = text[max(0, pos - 300) : pos]
        parts = [p for p in _SENT_SPLIT.split(chunk) if p.strip()]
        picked = parts[-n:]
        out = "".join(picked).strip()
        return out[-200:] if len(out) > 200 else out
    chunk = text[pos : pos + 300]
    parts = [p for p in _SENT_SPLIT.split(chunk) if p.strip()]
    picked = parts[:n]
    out = "".join(picked).strip()
    return out[:200]


def _neighbors(
    all_spans: Sequence[DialogueSpan],
    first: DialogueSpan,
    last: DialogueSpan,
    reliable: Sequence[DialogueSpan],
) -> list[tuple[str, str]]:
    before = [s for s in reliable if s.end <= first.start]
    after = [s for s in reliable if s.start >= last.end]
    out: list[tuple[str, str]] = []
    for s in before[-2:]:
        out.append((s.speaker_hint, s.content))
    for s in after[:1]:
        out.append((s.speaker_hint, s.content))
    return out


def _collect_candidates(
    spans: Sequence[DialogueSpan],
    extra: Sequence[str] | None,
    max_n: int,
) -> list[str]:
    counts: dict[str, int] = {}
    for s in spans:
        name = s.speaker_hint
        if is_noise_speaker(name):
            continue
        counts[name] = counts.get(name, 0) + 1
    ordered = sorted(counts.keys(), key=lambda k: -counts[k])
    if extra:
        for e in extra:
            e = (e or "").strip()
            if e and e not in ordered and not is_noise_speaker(e):
                ordered.insert(0, e)
    return ordered[: max_n - 1]
