"""CLUENER person NER + substring clustering for character inventory."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from src.domain.novel.character_policy import HONOR_SUFFIXES
from src.domain.novel.dialogue_span import is_noise_speaker

logger = logging.getLogger("agent")

_HONOR = HONOR_SUFFIXES

_MODEL = None
_TOKENIZER = None
_ID2LABEL = None
_DEVICE = None


@dataclass
class Mention:
    text: str
    start: int
    end: int
    source: str = "cluener"
    score: float = 0.0  # softmax 概率（name 标签），R1 新增：供 min_conf 过滤


@dataclass
class DraftCluster:
    cluster_id: str
    surfaces: list[str]
    count: int
    evidence: list[str] = field(default_factory=list)
    # V4 实体本体：簇内 surfaces 对应的属性挂接（name → [{type, value}] 合并）
    attributes: list[dict] = field(default_factory=list)

    def primary(self) -> str:
        # Prefer longest surface as draft canonical
        return sorted(self.surfaces, key=lambda s: (-len(s), s))[0] if self.surfaces else ""


def _strip_honor(name: str) -> str:
    s = (name or "").strip()
    for h in _HONOR:
        if s.endswith(h) and len(s) > len(h) + 1:
            return s[: -len(h)]
    return s


def _edit_distance_leq1(a: str, b: str) -> bool:
    """编辑距离 ≤1（错字/删字近邻）。R2: 替代子串无条件合并。

    温水佳树 vs 温水（长度差 2）→ False，不预合并（交给 LLM 判断）。
    利姆路 vs 利姆露（等长 diff 1）→ True，合并。
    """
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if abs(len(a) - len(b)) == 1:
        shorter, longer = (a, b) if len(a) < len(b) else (b, a)
        return any(shorter == longer[:i] + longer[i + 1 :] for i in range(len(longer)))
    return False


def _load_cluener(device: str = "cpu"):
    global _MODEL, _TOKENIZER, _ID2LABEL, _DEVICE
    if _MODEL is not None and device == _DEVICE:
        return _MODEL, _TOKENIZER, _ID2LABEL
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    model_id = "uer/roberta-base-finetuned-cluener2020-chinese"
    logger.info("Loading CLUENER NER: %s on %s", model_id, device)
    tok = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForTokenClassification.from_pretrained(model_id)
    model.to(device)
    model.eval()
    _TOKENIZER = tok
    _MODEL = model
    _ID2LABEL = model.config.id2label
    _DEVICE = device
    return _MODEL, _TOKENIZER, _ID2LABEL


def extract_person_mentions(
    text: str,
    *,
    device: str = "cpu",
    chunk: int = 450,
    min_conf: float = 0.5,
) -> list[Mention]:
    """Run CLUENER and return PERSON-like name spans with softmax confidence.

    ``min_conf`` filters low-confidence mentions (fragments like 来水管 /
    placeholders like 女生A are typically low-probability name labels).
    """
    import torch

    if not (text or "").strip():
        return []
    model, tok, id2label = _load_cluener(device)
    mentions: list[Mention] = []
    step = max(50, chunk - 50)

    for off in range(0, len(text), step):
        piece = text[off : off + chunk]
        if not piece.strip():
            continue
        enc = tok(piece, return_tensors="pt", truncation=True, max_length=512)
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            logits = model(**enc).logits[0]
        probs = torch.softmax(logits, dim=-1)  # [seq, num_labels]
        pred = probs.argmax(-1).tolist()
        tokens = tok.convert_ids_to_tokens(enc["input_ids"][0])

        buf: list[tuple[str, float]] = []  # (token, name-label prob)

        def flush():
            nonlocal buf
            if not buf:
                return
            # BPE/wordpiece 碎片（##xx）不是完整人名，直接丢弃
            if any(t.startswith("##") for t, _ in buf):
                buf = []
                return
            surface = (
                tok.convert_tokens_to_string([t for t, _ in buf])
                .replace(" ", "")
                .replace("[UNK]", "")
            )
            # 原文脚注标记（*、※）混入名字尾部 → 剥离（实测：井泽静江*）
            surface = surface.rstrip("*※＊")
            score = min(p for _, p in buf)  # 保守：取 token 最低概率
            buf = []
            if not surface or is_noise_speaker(surface) or len(surface) < 2:
                return
            if re.fullmatch(r"[\W_]+", surface):
                return
            if score < min_conf:
                return
            local = piece.find(surface)
            if local < 0:
                local = 0
            mentions.append(
                Mention(surface, off + local, off + local + len(surface), "cluener", score)
            )

        for ti, (tid, label_id) in enumerate(zip(tokens, pred)):
            if tid in ("[CLS]", "[SEP]", "[PAD]"):
                flush()
                continue
            lab = str(id2label[label_id])
            if lab in ("B-name", "I-name", "B-NAME", "I-NAME", "B-PER", "I-PER"):
                if lab.startswith("B"):
                    flush()
                buf.append((tid, float(probs[ti, label_id].item())))
            else:
                flush()
        flush()

    uniq = {(m.start, m.end, m.text): m for m in mentions}
    return list(uniq.values())


def _sample_positions(positions: list[int], max_n: int, min_gap: int = 5000) -> list[int]:
    """跨场景多样性抽样：优先选间隔 ≥ min_gap 的出现（不同章节/场景）。

    不足时用剩余位置补足，保证凑满 max_n（LLM 判断依据充足）。
    """
    if not positions:
        return []
    picked: list[int] = []
    last = None
    for p in positions:
        if len(picked) >= max_n:
            break
        if last is None or p - last >= min_gap:
            picked.append(p)
            last = p
    for p in positions:
        if len(picked) >= max_n:
            break
        if p not in picked:
            picked.append(p)
    return picked


def cluster_mentions(
    mentions: list[Mention],
    *,
    min_mentions: int = 2,
    text: str = "",
    evidence_per_cluster: int = 6,
    evidence_window: int = 100,
    name_attributes: dict[str, list[dict]] | None = None,
) -> list[DraftCluster]:
    """Union-find on substring / honorific; attach diverse evidence snippets.

    Evidence per cluster defaults to 6-8 short windows drawn across chapters
    (position gap ≥ 5000) — enough context for the LLM to disambiguate
    full name / short form / shared-surname characters.

    ``name_attributes`` (name → [{type, value}]) 来自粗扫 V4 实体本体：
    簇内 surfaces 的属性合并挂到簇上，供归一裁决属性挂接合理性。
    """
    counts: Counter[str] = Counter()
    positions: dict[str, list[int]] = defaultdict(list)
    for m in mentions:
        name = _strip_honor(m.text)
        if is_noise_speaker(name) or len(name) < 2:
            continue
        counts[name] += 1
        positions[name].append(m.start)

    names = list(counts.keys())
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a in names:
        for b in names:
            if a >= b:
                continue
            # R2: 只合并编辑距离 ≤1 的近邻（错字/删字），不再做子串无条件合并。
            # 全名↔简称（温水佳树 vs 温水）不预合并——拆并决策交给 LLM 归一。
            if _edit_distance_leq1(a, b) and min(len(a), len(b)) >= 2:
                union(a, b)

    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[find(n)].append(n)

    clusters: list[DraftCluster] = []
    for i, members in enumerate(groups.values()):
        total = sum(counts[m] for m in members)
        if total < min_mentions and max(len(m) for m in members) < 3:
            continue
        if total < min_mentions:
            # keep longer proper names even if rare
            if max(len(m) for m in members) < 3:
                continue
        surfaces = sorted(set(members), key=lambda s: (-counts[s], -len(s), s))
        # V4：簇内 surfaces 的属性合并挂接（跨 surface 去重累积）
        attrs: list[dict] = []
        if name_attributes:
            seen_attr: set[tuple[str, str]] = set()
            for surf in surfaces:
                for a in name_attributes.get(surf) or []:
                    if not isinstance(a, dict):
                        continue
                    key = (str(a.get("type") or ""), str(a.get("value") or ""))
                    if key[0] and key[1] and key not in seen_attr:
                        seen_attr.add(key)
                        attrs.append({"type": key[0], "value": key[1]})
        evid: list[str] = []
        if text:
            for surf in surfaces:
                positions_sorted = sorted(set(positions.get(surf, [])))
                for pos in _sample_positions(
                    positions_sorted, evidence_per_cluster
                ):
                    a = max(0, pos - evidence_window)
                    b = min(len(text), pos + len(surf) + evidence_window)
                    snip = text[a:b].replace("\n", " ").strip()
                    if snip and snip not in evid:
                        evid.append(snip)
                    if len(evid) >= evidence_per_cluster:
                        break
                if len(evid) >= evidence_per_cluster:
                    break
        clusters.append(
            DraftCluster(
                cluster_id=f"c{i+1}",
                surfaces=surfaces,
                count=total,
                evidence=evid,
                attributes=attrs,
            )
        )

    clusters.sort(key=lambda c: (-c.count, c.primary()))
    # re-id after sort for stable LLM prompts
    for i, c in enumerate(clusters):
        c.cluster_id = f"c{i+1}"
    return clusters
