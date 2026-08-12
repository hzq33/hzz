"""NovelParser — parse raw Markdown into structured NovelDocument.

Detects chapters, extracts metadata (title, author), and produces
a unified NovelDocument regardless of the original file format.

Chapter detection uses a hybrid strategy:
  1. Hardcoded regex patterns (fast path, free)
  2. Completeness check on regex result:
     - chapters >= 3
     - leading text before first chapter < threshold (catch missing prologue)
     - trailing text after last chapter < threshold (catch missing afterword)
  3. LLM fallback (slow path, ¥0.008): sample head+tail → infer regex → split
"""

from __future__ import annotations

import logging
import re

from src.domain.novel.models import Chapter, NovelDocument

logger = logging.getLogger("agent")


class NovelParser:
    """Parse raw Markdown into a structured NovelDocument.

    Handles multiple chapter-heading styles common in Chinese novels:
    - Markdown headings: # 第一章, ## Chapter 1
    - Chinese chapter markers: 第一章、第一回、第1章
    - Numbered markers: （一）、(1)、一、
    - Heuristic: short standalone lines followed by body text

    Usage:
        parser = NovelParser()
        doc = parser.parse(raw_md, doc_id="镜湖风云录", source_format="md")
    """

    # ── Chapter heading patterns (ordered by priority) ────

    _CHAPTER_PATTERNS: list[re.Pattern] = [
        # Markdown headings: # 第一章, ## Chapter 1
        re.compile(r"^#{1,3}\s+(.+)", re.MULTILINE),
        # Chinese chapter markers: 第一章、第一回、第1章、第一百二十回
        re.compile(
            r"^第[一二三四五六七八九十百千零两\d]+[章回节卷篇部]",
            re.MULTILINE,
        ),
        # English chapter: Chapter 1, CHAPTER ONE
        re.compile(r"^(?:CHAPTER|Chapter|chapter)\s+(?:[IVXLCDM]+|\d+|[A-Z][a-z]+)", re.MULTILINE),
        # Numbered in parens: （一）、(1)、【一】
        re.compile(r"^[（(【]\s*[一二三四五六七八九十百千零两\d]+\s*[）)】]", re.MULTILINE),
        # Bare number + punctuation: 一、 1. 1、
        re.compile(r"^[一二三四五六七八九十百千零两\d]+[、.．]\s*$", re.MULTILINE),
    ]

    # Minimum characters for a line to be considered a chapter heading
    _MIN_HEADING_LEN = 2
    _MAX_HEADING_LEN = 60

    # Completeness check thresholds (chars of unattributed text allowed)
    _COMPLETENESS_THRESHOLD = 2000
    _COMPLETENESS_MIN_CHAPTERS = 3

    def parse(
        self,
        raw_md: str,
        doc_id: str = "",
        source_format: str = "",
    ) -> NovelDocument:
        """Parse raw Markdown into a NovelDocument.

        Args:
            raw_md: Raw Markdown text (after format conversion).
            doc_id: Document identifier (defaults to filename stem).
            source_format: Original file format (epub/txt/md/pdf).

        Returns:
            NovelDocument with chapters and metadata populated.
        """
        title = self._extract_title(raw_md, doc_id)
        author = self._extract_author(raw_md)
        chapters = self._detect_chapters(raw_md, doc_id)

        return NovelDocument(
            doc_id=doc_id,
            title=title,
            author=author,
            source_format=source_format,
            raw_md=raw_md,
            chapters=chapters,
            metadata={
                "total_words": sum(len(ch.text) for ch in chapters),
                "total_chapters": len(chapters),
            },
        )

    # ── Chapter detection ─────────────────────────────────

    def _detect_chapters(self, raw_md: str, doc_id: str) -> list[Chapter]:
        """Detect chapters using multiple strategies.

        Falls back to single-chapter if no headings are found.
        """
        # Strategy 1: Try each pattern in order
        for pattern in self._CHAPTER_PATTERNS:
            matches = list(pattern.finditer(raw_md))
            if len(matches) >= 2:  # At least 2 chapters to split
                return self._build_chapters_from_matches(raw_md, matches, doc_id)

        # Strategy 2: Heuristic — look for short standalone lines
        # that look like headings (short, no punctuation at end)
        chapters = self._heuristic_chapter_detection(raw_md, doc_id)
        if chapters:
            return chapters

        # Strategy 3: Force-split large texts into pseudo-chapters at blank lines
        chapters = self._split_large_text(raw_md, doc_id)
        if chapters:
            return chapters

        # Strategy 4: Single chapter (entire text)
        return [
            Chapter(
                chapter_id=f"{doc_id}_ch_0",
                title="正文",
                order=0,
                text=raw_md.strip(),
            )
        ]

    def _build_chapters_from_matches(
        self,
        raw_md: str,
        matches: list[re.Match],
        doc_id: str,
    ) -> list[Chapter]:
        """Build Chapter list from regex match positions."""
        chapters: list[Chapter] = []

        for i, match in enumerate(matches):
            title = self._clean_heading(match.group(0))
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_md)

            # Extract chapter body (between this heading and next)
            body = raw_md[start:end].strip()

            # Skip empty chapters
            if not body:
                continue

            chapters.append(Chapter(
                chapter_id=f"{doc_id}_ch_{i}",
                title=title,
                order=i,
                text=body,
            ))

        return chapters

    def _heuristic_chapter_detection(
        self, raw_md: str, doc_id: str
    ) -> list[Chapter]:
        """Heuristic chapter detection for novels without clear headings.

        Looks for short standalone lines (potential chapter titles)
        separated by blank lines from body text.
        """
        paragraphs = re.split(r"\n{2,}", raw_md)
        chapters: list[Chapter] = []
        chapter_idx = 0

        i = 0
        while i < len(paragraphs):
            para = paragraphs[i].strip()

            # Check if this paragraph looks like a chapter heading
            if self._is_likely_heading(para):
                # Collect subsequent paragraphs as chapter body
                body_parts: list[str] = []
                i += 1
                while i < len(paragraphs) and not self._is_likely_heading(paragraphs[i].strip()):
                    body_parts.append(paragraphs[i].strip())
                    i += 1

                body = "\n\n".join(body_parts)
                if body:
                    chapters.append(Chapter(
                        chapter_id=f"{doc_id}_ch_{chapter_idx}",
                        title=para,
                        order=chapter_idx,
                        text=body,
                    ))
                    chapter_idx += 1
            else:
                i += 1

        return chapters

    def _split_large_text(self, raw_md: str, doc_id: str) -> list[Chapter]:
        """Split oversized single-chapter text into pseudo-chapters at blank lines.

        Only triggers when text > 30K chars and has no detected chapter structure.
        """
        text = raw_md.strip()
        if len(text) < 30_000:
            return []

        # Split on 2+ blank lines
        parts = re.split(r"\n{3,}", text)
        if len(parts) < 3:
            return []

        chapters = []
        for i, part in enumerate(parts):
            part = part.strip()
            if not part or len(part) < 200:
                continue
            # Use first line (up to 30 chars) as pseudo-title
            first_line = part.split("\n")[0].strip()[:30]
            chapters.append(Chapter(
                chapter_id=f"{doc_id}_ch_{i}",
                title=first_line or f"第{i+1}段",
                order=i,
                text=part,
            ))
        return chapters if len(chapters) >= 2 else []

    def _is_likely_heading(self, text: str) -> bool:
        """Check if a line looks like a chapter heading."""
        if not text:
            return False
        # Too long to be a heading
        if len(text) > self._MAX_HEADING_LEN:
            return False
        # Too short
        if len(text) < self._MIN_HEADING_LEN:
            return False
        # Contains sentence-ending punctuation (likely body text)
        if text[-1] in "。！？…":
            return False
        # Contains dialogue quotes (likely body text)
        if any(q in text for q in '"「「『'):
            return False
        # Looks like a heading: short, no ending punctuation
        return True

    # ── Completeness check ────────────────────────────────

    def check_completeness(
        self, raw_md: str, chapters: list[Chapter]
    ) -> tuple[bool, str]:
        """Check if chapter detection captured the full document.

        Detects two common failure modes of hardcoded regex:
        - Missing prologue/序章: leading text before first chapter too long
        - Missing afterword/后记: trailing text after last chapter too long

        Args:
            raw_md: Full preprocessed text.
            chapters: Detected chapters.

        Returns:
            (is_complete, reason). reason is empty string when complete.
        """
        if len(chapters) < self._COMPLETENESS_MIN_CHAPTERS:
            return False, f"too few chapters ({len(chapters)} < {self._COMPLETENESS_MIN_CHAPTERS})"

        # Check leading text (before first chapter title)
        first_title = chapters[0].title
        first_pos = raw_md.find(first_title)
        if first_pos < 0:
            # Title may have been cleaned (e.g. markdown stripped); try
            # progressively shorter prefixes
            for prefix_len in range(len(first_title), 2, -1):
                first_pos = raw_md.find(first_title[:prefix_len])
                if first_pos >= 0:
                    break
        if first_pos > self._COMPLETENESS_THRESHOLD:
            return False, f"leading text before first chapter too long ({first_pos} chars, likely missing prologue)"

        # Check trailing text (after last chapter title)
        last_title = chapters[-1].title
        last_pos = raw_md.rfind(last_title)
        if last_pos < 0:
            for prefix_len in range(len(last_title), 2, -1):
                last_pos = raw_md.rfind(last_title[:prefix_len])
                if last_pos >= 0:
                    break
        if last_pos >= 0:
            trailing = len(raw_md) - (last_pos + len(last_title))
            if trailing > self._COMPLETENESS_THRESHOLD:
                return False, f"trailing text after last chapter too long ({trailing} chars, likely missing afterword)"

        return True, ""

    # ── Metadata extraction ───────────────────────────────

    def _extract_title(self, raw_md: str, doc_id: str) -> str:
        """Extract book title from content or fallback to doc_id."""
        # Try first heading
        m = re.search(r"^#\s+(.+)", raw_md, re.MULTILINE)
        if m:
            title = m.group(1).strip()
            # Filter out generic headings
            if title and not title.startswith("第") and len(title) < 30:
                return title

        # Try first line
        first_line = raw_md.strip().split("\n")[0].strip()
        if first_line and len(first_line) < 30:
            # Remove markdown heading markers
            first_line = re.sub(r"^#+\s+", "", first_line)
            return first_line

        return doc_id or "未知书名"

    def _extract_author(self, raw_md: str) -> str:
        """Try to extract author name from metadata patterns."""
        patterns = [
            r"作者[：:]\s*(.+?)(?:\n|$)",
            r"著[者]?\s*[：:]\s*(.+?)(?:\n|$)",
            r"by\s+(.+?)(?:\n|$)",
            r"©\s*(.+?)(?:\n|$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, raw_md)
            if m:
                author = m.group(1).strip()
                if author and len(author) < 20:
                    return author
        return ""

    # ── Helpers ───────────────────────────────────────────

    def _clean_heading(self, raw_heading: str) -> str:
        """Clean a raw heading match into a clean title string."""
        # Remove markdown heading markers
        text = re.sub(r"^#+\s+", "", raw_heading)
        # Remove leading/trailing whitespace
        text = text.strip()
        # Truncate if too long
        if len(text) > self._MAX_HEADING_LEN:
            text = text[: self._MAX_HEADING_LEN] + "…"
        return text
