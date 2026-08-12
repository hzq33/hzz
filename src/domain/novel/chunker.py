"""MD cleaning and sliding-window chunking for novel text.

Transforms raw Markdown (e.g., from epub/txt/docx conversion) into
cleaned, structured NovelBlock records ready for vector indexing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.domain.novel.models import (
    BLOCK_NARRATIVE,
    NovelBlock,
)

# ── Custom marker patterns ──────────────────────────────────

_CUSTOM_MARKER = re.compile(r"【(scene|narrative|dialogue)】(.*)")
_DIALOGUE_LINE = re.compile(
    r'^[""「」『』](.+?)[""「」『』]\s*(?:[—\-]\s*(.+))?$'
)
# Also match: 角色名："对话内容"  or 角色名：——对话内容
_NAMED_DIALOGUE = re.compile(r'^(.+?)[：:]\s*[""「」](.+?)[""「」]')


def _is_cjk_char(ch: str) -> bool:
    return bool(ch) and ("\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff")


def match_known_persons(text: str, names: list[str]) -> list[str]:
    """公开入口：分块时的人物标记匹配（供管线外工具/脚本复用）。

    分层规则与 _match_known_persons 一致（≥3 字子串 / 2 字防子串污染 / 1 字忽略）；
    脚本与管线外代码应使用本函数而非私有实现。
    """
    return _match_known_persons(text, names)


def _match_known_persons(text: str, names: list[str]) -> list[str]:
    """Tiered name matching for narrative all_person tagging.

    - Names with >= 3 chars: plain substring match (low noise).
    - 2-char names: must not be a substring of any longer known name
      (so "露伊" never matches inside "露伊莎"). Chinese given names are
      almost always CJK-flanked in prose ("青年阴阳师晴明提着灯"), so
      requiring non-CJK neighbors wrongly drops every 2-char character —
      the character list from LLM inventory is already noise-filtered,
      so a direct match is the right trade-off.
    - 1-char names are ignored (too noisy).
    """
    if not text or not names:
        return []
    long_names = sorted(
        (n for n in names if n and len(n) >= 3), key=len, reverse=True
    )
    short_names = [n for n in names if n and len(n) == 2]
    found: list[str] = []
    for n in long_names:
        if n in text:
            found.append(n)
    for n in short_names:
        if any(n in long for long in long_names):
            continue
        if n in text:
            found.append(n)
    return found

# ── Standard Chinese dialogue detection ─────────────────────
# Matches lines that look like dialogue in standard Chinese novels
_STANDARD_DIALOGUE_LINE = re.compile(
    r'^[""「」].+?[""「」]|'           # "对话" or "对话"
    r'.+?[：:]\s*[""「」].+?[""「」]'   # 角色："对话"
)

# ── MD syntax stripping ─────────────────────────────────────

_MD_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),  # bold
    (re.compile(r"\*(.+?)\*"), r"\1"),        # italic
    (re.compile(r"`(.+?)`"), r"\1"),          # inline code
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),  # headings (# → "")
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),  # list bullets
    (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),  # numbered list
    (re.compile(r">\s?"), ""),                 # blockquote
    (re.compile(r"~~(.+?)~~"), r"\1"),         # strikethrough
    (re.compile(r"<[^>]+>"), ""),              # HTML tags
]


@dataclass
class CleanedMD:
    """Result of MD cleaning, ready for chunking."""

    text: str = ""                        # fully cleaned, no MD syntax
    chapter_title: str = ""
    scenes: list[str] = field(default_factory=list)
    dialogue_blocks: list[str] = field(default_factory=list)
    source_prefix: str = ""               # e.g. "《星辰之下》"


class MDCleaner:
    """Clean Markdown syntax and extract structured elements.

    Usage:
        cleaner = MDCleaner()
        result = cleaner.clean(raw_md, doc_id="星辰之下")
        for block in chunker.chunk(result):
            store.index(block)
    """

    def clean(self, raw_md: str, doc_id: str = "") -> CleanedMD:
        """Clean raw MD and extract structured elements.

        Args:
            raw_md: Raw Markdown content (e.g., from epub → md conversion).
            doc_id: Document/book identifier (used for source_prefix).

        Returns:
            CleanedMD with extracted scenes, dialogues, and headings.
        """
        result = CleanedMD(source_prefix=f"《{doc_id}》" if doc_id else "")

        # Step 1: Extract chapter titles from headings
        heading_match = re.search(r"^#\s+(.+)", raw_md, re.MULTILINE)
        if heading_match:
            result.chapter_title = heading_match.group(1).strip()

        # Step 2: Extract custom markers before stripping
        text = raw_md
        for match in _CUSTOM_MARKER.finditer(text):
            marker_type = match.group(1)
            content = match.group(2).strip() if match.group(2) else ""
            if marker_type == "scene" and content:
                result.scenes.append(content)

        # Collect dialogue blocks (text between 【dialogue】 and next marker)
        dia_pattern = re.compile(r"【dialogue】\s*\n(.*?)(?=\n【|$)", re.DOTALL)
        for match in dia_pattern.finditer(text):
            block = match.group(1).strip()
            if block:
                result.dialogue_blocks.append(block)

        # Auto-detect dialogue paragraphs from standard novel text
        # (when no 【dialogue】 markers are present)
        if not result.dialogue_blocks:
            result.dialogue_blocks = self._auto_detect_dialogue_blocks(text)

        # Step 3: Strip MD syntax
        for pattern, replacement in _MD_PATTERNS:
            text = pattern.sub(replacement, text)

        # Step 4: Remove custom markers entirely
        text = _CUSTOM_MARKER.sub("", text)

        # Step 5: Merge blank lines (max 1 consecutive blank line)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Step 6: Trim whitespace
        text = text.strip()

        result.text = text
        return result

    def _auto_detect_dialogue_blocks(self, text: str) -> list[str]:
        """Auto-detect dialogue paragraphs from standard novel text.

        Strategy:
        1. Scan line-by-line. Dialogue lines are collected into a group.
        2. Narrative lines BETWEEN dialogue lines are kept as context
           (essential for speaker inference in Chinese-translated Japanese
           light novels, where speaker and 「quote」 sit on separate lines).
        3. Flush a group when we hit >2 consecutive narrative lines or EOF.
        4. Pure-narrative regions (no dialogue yet) are skipped.

        This handles both:
          - Chinese originals: "你好。"林晚晴说。 (named speaker, consecutive)
          - CN-translated JP light novels: 叙事行\\n「对话」 (alternating, context-inferred)

        Args:
            text: Raw or cleaned text.

        Returns:
            List of dialogue block strings (each may include narrative context).
        """
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        dialogue_blocks: list[str] = []
        current_group: list[str] = []
        has_dialogue = False
        consecutive_narrative = 0
        _MAX_NARRATIVE_BETWEEN = 2  # flush if this many pure-narrative lines in a row

        for line in lines:
            if _STANDARD_DIALOGUE_LINE.search(line):
                current_group.append(line)
                has_dialogue = True
                consecutive_narrative = 0
            else:
                # Narrative line
                if has_dialogue:
                    consecutive_narrative += 1
                    if consecutive_narrative > _MAX_NARRATIVE_BETWEEN:
                        # Too much narrative — flush current group
                        if current_group:
                            dialogue_blocks.append("\n".join(current_group))
                        current_group = []
                        has_dialogue = False
                        consecutive_narrative = 0
                    else:
                        # Keep narrative as context for speaker inference
                        current_group.append(line)
                # else: pure narrative before any dialogue — skip

        # Flush remaining group
        if has_dialogue and current_group:
            dialogue_blocks.append("\n".join(current_group))

        return dialogue_blocks


# ── Sliding window chunker ───────────────────────────────────


class NovelChunker:
    """Sliding-window chunker for cleaned MD text.

    Produces narrative-type NovelBlock records from cleaned prose.
    Uses 10% overlap to prevent context fractures at window boundaries.
    """

    def __init__(
        self,
        chunk_size: int = 500,
        overlap: int = 50,
        min_chunk_size: int = 150,
        known_characters: list[str] | None = None,
    ):
        """Configure chunking parameters.

        Args:
            chunk_size: Target characters per chunk (maps to ~300-800 tokens).
            overlap: Overlap between consecutive chunks (default 10%).
            min_chunk_size: Minimum characters for a chunk. Smaller chunks merged.
            known_characters: Optional name list for narrative all_person tagging.
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size
        self.known_characters = list(known_characters or [])

    def chunk(
        self,
        cleaned: CleanedMD,
        doc_id: str = "",
        chapter_index: int = 0,
    ) -> list[NovelBlock]:
        """Chunk cleaned text into NovelBlock records.

        Args:
            cleaned: CleanedMD result from MDCleaner.clean().
            doc_id: Document identifier for global_id prefix.
            chapter_index: Zero-based chapter order — included in global_id so
                multi-chapter ingest does not collide on ``_narrative_0``.

        Returns:
            List of NovelBlock records (block_type=narrative).
        """
        blocks: list[NovelBlock] = []
        chapter = cleaned.chapter_title or "正文"

        # Helper to estimate token count (Chinese: ~1 char = 1 token; English: ~0.3 token/char)
        def est_tokens(text: str) -> int:
            chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
            other = len(text) - chinese
            return chinese + int(other * 0.3)

        # Split text into paragraphs
        paragraphs = [p.strip() for p in cleaned.text.split("\n") if p.strip()]
        if not paragraphs:
            return blocks

        # Sliding window over paragraphs
        idx = 0
        chunk_num = 0
        while idx < len(paragraphs):
            # Accumulate paragraphs until chunk_size reached
            chunk_text = ""
            start_idx = idx
            while idx < len(paragraphs):
                candidate = chunk_text + paragraphs[idx]
                if len(candidate) > self.chunk_size and chunk_text:
                    break
                chunk_text = candidate
                idx += 1

            # Skip chunks that are too small (merge into next)
            if len(chunk_text) < self.min_chunk_size and idx < len(paragraphs):
                continue

            source = (
                f"{cleaned.source_prefix} {chapter}"
                if cleaned.source_prefix
                else chapter
            )
            source += f" 第{chunk_num + 1}段"

            if doc_id:
                gid = f"{doc_id}_c{chapter_index:03d}_n{chunk_num:04d}"
            else:
                gid = f"c{chapter_index:03d}_n{chunk_num:04d}"

            block = NovelBlock(
                global_id=gid,
                doc_id=doc_id,
                source=source,
                chapter_title=chapter,
                block_type=BLOCK_NARRATIVE,
                narrative_text=chunk_text,
                vec_text_narrative=chunk_text,
                all_person=self._detect_persons(chunk_text),
                token_length=est_tokens(chunk_text),
            )
            blocks.append(block)
            chunk_num += 1

            # Overlap: step back by character budget mapped to paragraphs.
            if idx < len(paragraphs):
                window = paragraphs[start_idx:idx]
                if self.overlap <= 0 or len(window) <= 1:
                    continue
                # Prefer character-based overlap when possible.
                back_chars = 0
                step_back = 0
                for p in reversed(window[:-1]):
                    back_chars += len(p)
                    step_back += 1
                    if back_chars >= self.overlap:
                        break
                step_back = max(1, step_back)
                idx = max(start_idx + 1, idx - step_back)

        return blocks

    def _detect_persons(self, text: str) -> list[str]:
        """Detect known character names mentioned in a narrative chunk."""
        return _match_known_persons(text, self.known_characters)

    def set_known_characters(self, names: list[str]) -> None:
        """Update the character dictionary used for all_person tagging."""
        self.known_characters = list(names or [])


# ── Parent / Child hierarchical chunker ──────────────────────


_SENT_END = re.compile(r"(?<=[。！？」』\"”])")


def _est_tokens(text: str) -> int:
    chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - chinese
    return chinese + int(other * 0.3)


def _split_sentences(text: str) -> list[str]:
    """Split on Chinese/English sentence enders; keep delimiters attached."""
    text = (text or "").strip()
    if not text:
        return []
    parts = _SENT_END.split(text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out if out else [text]


def _pack_children_from_sentences(
    sentences: list[str],
    *,
    child_chars: int,
    min_child: int,
    max_child: int,
) -> list[str]:
    """Group sentences into child chunks by character budget (sentence-boundary)."""
    if not sentences:
        return []
    children: list[str] = []
    buf = ""
    for sent in sentences:
        candidate = buf + sent
        if buf and len(candidate) > child_chars and len(buf) >= min_child:
            children.append(buf)
            buf = sent
        else:
            buf = candidate
        if len(buf) >= max_child:
            children.append(buf)
            buf = ""
    if buf:
        if children and len(buf) < min_child:
            children[-1] = children[-1] + buf
        else:
            children.append(buf)
    return children


class HierarchicalChunker:
    """Parent/Child narrative chunker.

    Parent = evidence unit (stored; vector optional).
    Child = retrieval unit (always vectorized), ``parent_id`` → Parent.
    """

    def __init__(
        self,
        parent_chars: int = 800,
        parent_overlap_chars: int = 80,
        child_chars: int = 150,
        min_child_chars: int = 80,
        max_child_chars: int = 220,
        index_parents: bool = False,
        known_characters: list[str] | None = None,
        chapter_prefix_in_vec: bool = True,
    ):
        self.parent_chars = max(200, int(parent_chars))
        self.parent_overlap_chars = max(0, int(parent_overlap_chars))
        self.child_chars = max(40, int(child_chars))
        self.min_child_chars = max(20, int(min_child_chars))
        self.max_child_chars = max(self.child_chars, int(max_child_chars))
        self.index_parents = bool(index_parents)
        self.known_characters = list(known_characters or [])
        self.chapter_prefix_in_vec = bool(chapter_prefix_in_vec)

    def set_known_characters(self, names: list[str]) -> None:
        self.known_characters = list(names or [])

    def _detect_persons(self, text: str) -> list[str]:
        return _match_known_persons(text, self.known_characters)

    def chunk(
        self,
        cleaned: CleanedMD,
        doc_id: str = "",
        chapter_index: int = 0,
    ) -> list[NovelBlock]:
        """Return Parent blocks followed by their Child blocks (flat list)."""
        chapter = cleaned.chapter_title or "正文"
        paragraphs = [p.strip() for p in cleaned.text.split("\n") if p.strip()]
        if not paragraphs:
            return []

        parents = self._build_parents(
            paragraphs,
            cleaned=cleaned,
            doc_id=doc_id,
            chapter_index=chapter_index,
            chapter=chapter,
        )
        blocks: list[NovelBlock] = []
        for parent in parents:
            children = self._build_children(parent, chapter=chapter)
            # link siblings
            for i, ch in enumerate(children):
                if i > 0:
                    ch.prev_id = children[i - 1].global_id
                    children[i - 1].next_id = ch.global_id
            blocks.append(parent)
            blocks.extend(children)
        return blocks

    def _build_parents(
        self,
        paragraphs: list[str],
        *,
        cleaned: CleanedMD,
        doc_id: str,
        chapter_index: int,
        chapter: str,
    ) -> list[NovelBlock]:
        parents: list[NovelBlock] = []
        idx = 0
        parent_num = 0
        max_parent = int(self.parent_chars * 1.5)  # soft upper ~1200 when parent=800

        while idx < len(paragraphs):
            chunk_text = ""
            start_idx = idx
            while idx < len(paragraphs):
                candidate = chunk_text + paragraphs[idx]
                if chunk_text and len(candidate) > self.parent_chars:
                    if len(chunk_text) >= self.parent_chars // 2:
                        break
                if chunk_text and len(candidate) > max_parent:
                    break
                chunk_text = candidate
                idx += 1

            if len(chunk_text) < 50 and idx < len(paragraphs):
                continue
            if not chunk_text.strip():
                continue

            source = (
                f"{cleaned.source_prefix} {chapter}"
                if cleaned.source_prefix
                else chapter
            )
            source += f" 第{parent_num + 1}段"
            if doc_id:
                gid = f"{doc_id}_c{chapter_index:03d}_n{parent_num:04d}"
            else:
                gid = f"c{chapter_index:03d}_n{parent_num:04d}"

            parent = NovelBlock(
                global_id=gid,
                doc_id=doc_id,
                source=source,
                chapter_title=chapter,
                block_type=BLOCK_NARRATIVE,
                narrative_text=chunk_text,
                # Hybrid index: Parent vectors optional
                vec_text_narrative=chunk_text if self.index_parents else "",
                all_person=self._detect_persons(chunk_text),
                token_length=_est_tokens(chunk_text),
                granularity="parent",
                parent_id="",
            )
            parents.append(parent)
            parent_num += 1

            if idx < len(paragraphs) and self.parent_overlap_chars > 0:
                window = paragraphs[start_idx:idx]
                if len(window) > 1:
                    back_chars = 0
                    step_back = 0
                    for p in reversed(window[:-1]):
                        back_chars += len(p)
                        step_back += 1
                        if back_chars >= self.parent_overlap_chars:
                            break
                    step_back = max(1, step_back)
                    idx = max(start_idx + 1, idx - step_back)
        return parents

    def _build_children(self, parent: NovelBlock, *, chapter: str) -> list[NovelBlock]:
        sentences = _split_sentences(parent.narrative_text)
        pieces = _pack_children_from_sentences(
            sentences,
            child_chars=self.child_chars,
            min_child=self.min_child_chars,
            max_child=self.max_child_chars,
        )
        if not pieces:
            pieces = [parent.narrative_text]

        children: list[NovelBlock] = []
        offset = 0
        for i, text in enumerate(pieces):
            # approximate char offsets within parent
            pos = parent.narrative_text.find(text[: min(40, len(text))], offset)
            if pos < 0:
                pos = offset
            start, end = pos, pos + len(text)
            offset = end

            prefix = f"【{chapter}】" if self.chapter_prefix_in_vec and chapter else ""
            vec = f"{prefix}{text}" if prefix else text
            child = NovelBlock(
                global_id=f"{parent.global_id}__s{i:03d}",
                doc_id=parent.doc_id,
                source=f"{parent.source}·子{i + 1}",
                chapter_title=parent.chapter_title,
                block_type=BLOCK_NARRATIVE,
                narrative_text=text,
                vec_text_narrative=vec,
                all_person=self._detect_persons(text),
                token_length=_est_tokens(text),
                granularity="child",
                parent_id=parent.global_id,
            )
            children.append(child)
        return children


def chunk_narrative_for_ingest(
    cleaned: CleanedMD,
    *,
    doc_id: str = "",
    chapter_index: int = 0,
    hierarchy: dict | None = None,
    flat_chunk_size: int = 500,
    flat_overlap: int = 50,
    known_characters: list[str] | None = None,
) -> list[NovelBlock]:
    """Dispatch flat vs hierarchical chunking from config dict."""
    hier = dict(hierarchy or {})
    if hier.get("enabled", True) and hier.get("child_chars"):
        chunker = HierarchicalChunker(
            parent_chars=int(hier.get("parent_chars", 800)),
            parent_overlap_chars=int(hier.get("parent_overlap_chars", 80)),
            child_chars=int(hier.get("child_chars", 150)),
            min_child_chars=int(hier.get("min_child_chars", 80)),
            max_child_chars=int(hier.get("max_child_chars", 220)),
            index_parents=bool(hier.get("index_parents", False)),
            known_characters=known_characters,
            chapter_prefix_in_vec=bool(hier.get("chapter_prefix_in_vec", True)),
        )
        return chunker.chunk(cleaned, doc_id=doc_id, chapter_index=chapter_index)

    chunker = NovelChunker(
        chunk_size=flat_chunk_size,
        overlap=flat_overlap,
        known_characters=known_characters,
    )
    return chunker.chunk(cleaned, doc_id=doc_id, chapter_index=chapter_index)


def preprocess_novel_md(
    raw_md: str,
    doc_id: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[NovelBlock]:
    """Full preprocessing pipeline: clean → chunk → NovelBlock records.

    This is the single entry point for converting raw MD into vector-ready
    NovelBlock records. Does NOT include dialogue/QA extraction (see dialogue.py).

    Args:
        raw_md: Raw Markdown content.
        doc_id: Document/book identifier.
        chunk_size: Target characters per chunk.
        overlap: Overlap between chunks.

    Returns:
        List of NovelBlock records (narrative type only).
    """
    cleaner = MDCleaner()
    chunker = NovelChunker(chunk_size=chunk_size, overlap=overlap)

    cleaned = cleaner.clean(raw_md, doc_id=doc_id)
    return chunker.chunk(cleaned, doc_id=doc_id)
