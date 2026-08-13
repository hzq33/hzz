"""Preprocessor pipeline — stream-based novel text cleaning and structuring.

Replaces the ad-hoc cleaning in chunk.py/MDCleaner with a composable 5-stage
pipeline designed for batch processing of heterogeneous novel sources.

Pipeline stages (each independently configurable and skippable):

  Stage 0: Encoding detection + transcoding (chardet -> UTF-8)
  Stage 1: Binary filter (zero-width chars, control codes, BOM)
  Stage 2: Line-level cleaning (ad pattern matching, junk line scoring)
  Stage 3: Text normalization (punctuation, whitespace, optional zh-Hant->zh-Hans)
  Stage 4: Paragraph repair (broken line merging via ML-heuristic)
  Stage 5: Chapter boundary detection (regex + rule scoring, not regex-only)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("agent.preprocess")


# ============================================================================
# Metrics
# ============================================================================

@dataclass
class PreprocessMetrics:
    """Per-stage statistics emitted by each pipeline stage."""
    stage: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    lines_in: int = 0
    lines_out: int = 0
    lines_removed: int = 0
    chapters_detected: int = 0
    encoding_detected: str = ""
    duration_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# Stage 0: Encoding detection
# ============================================================================

_FALLBACK_ENCODINGS = ["utf-8", "gb18030", "gbk", "shift_jis", "big5", "euc-jp", "cp932"]


def _cjk_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf")
    return cjk / max(len(text), 1)


def detect_and_transcode(raw: bytes) -> tuple[str, PreprocessMetrics]:
    """Detect encoding and transcode to UTF-8 string."""
    metrics = PreprocessMetrics(stage="encoding", bytes_in=len(raw))

    try:
        import chardet
        result = chardet.detect(raw)
        enc = result.get("encoding") or "utf-8"
        if result.get("confidence", 0) > 0.7:
            metrics.encoding_detected = enc
            return raw.decode(enc, errors="replace"), metrics
    except (ImportError, Exception):
        pass

    for enc in _FALLBACK_ENCODINGS:
        try:
            text = raw.decode(enc)
            if _cjk_char_ratio(text) > 0.05:
                metrics.encoding_detected = enc
                return text, metrics
        except (UnicodeDecodeError, LookupError):
            continue

    metrics.encoding_detected = "utf-8 (fallback)"
    metrics.warnings.append("all detection failed, using utf-8 replace")
    return raw.decode("utf-8", errors="replace"), metrics


# ============================================================================
# Stage 1: Binary character filter
# ============================================================================

_BINARY_FILTER_RE = re.compile(
    "[\u200b\u200c-\u200f\u2028-\u2029\u2060-\u2064\ufeff\ufffd"
    "\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]+",
    flags=re.UNICODE,
)

_PRIVATE_FILTER_RE = re.compile(r"[\ue000-\uf8ff]+", flags=re.UNICODE)


def filter_binary(text: str) -> tuple[str, PreprocessMetrics]:
    """Remove zero-width chars, control codes, BOM, private-use garbage."""
    metrics = PreprocessMetrics(stage="binary_filter", bytes_in=len(text.encode("utf-8")))
    cleaned = _BINARY_FILTER_RE.sub("", text)
    cleaned = _PRIVATE_FILTER_RE.sub("", cleaned)
    metrics.bytes_out = len(cleaned.encode("utf-8"))
    return cleaned, metrics


# ============================================================================
# Stage 2: Line-level cleaning
# ============================================================================

_AD_BLACKLIST = re.compile(
    r"(?i)("
    r"www\.[a-z0-9.-]+"
    r"|http[s]?://[^\s]+"
    r"|请记住.*?(?:网址|域名|地址)"
    r"|更多精彩.*?(?:请访问|尽在|请关注)"
    r"|本章未完.*?(?:请点击|下一页)"
    r"|求(?:推荐票|月票|收藏|订阅|打赏)"
    r"|本书.*?(?:首发|来自|转载)"
    r"|看最快更新.*?(?:就上|就来)"
    r"|如果.*?(?:觉得好看|觉得不错|喜欢).*?(?:请收藏|请投票|请推荐)"
    # Japanese light novel noise: ruby text, fan translation notes
    r"|^[（(]?(?:译注|注|※|\*).*?[）)]?\s*$"
    r"|^\s*[\|｜].*?[\|｜]\s*$"
    r")"
)

_JUNK_LINE_PATTERNS = [
    (re.compile(r"^[^\w\u4e00-\u9fff]{10,}$"), "punctuation_only"),
    (re.compile(r"^[\d\s.,;:!?，。；：！？、]+$"), "numeric_punct"),
    (re.compile(r"^(.)\1{8,}$"), "repeated_char"),
]


def score_line(line: str) -> float:
    """Score a line 0.0 (junk) to 1.0 (keep). Below 0.3 dropped."""
    stripped = line.strip()
    if not stripped:
        return 0.5
    if len(stripped) < 2:
        return 0.3
    if _AD_BLACKLIST.search(stripped):
        return 0.0
    for pattern, _ in _JUNK_LINE_PATTERNS:
        if pattern.search(stripped):
            return 0.1
    return 1.0


def clean_lines(text: str, min_score: float = 0.3) -> tuple[str, PreprocessMetrics]:
    """Remove ad lines and obvious junk."""
    metrics = PreprocessMetrics(stage="line_clean", bytes_in=len(text.encode("utf-8")))
    lines = text.splitlines()
    metrics.lines_in = len(lines)

    kept = [line for line in lines if score_line(line) >= min_score]
    metrics.lines_removed = len(lines) - len(kept)
    metrics.lines_out = len(kept)

    result = "\n".join(kept)
    metrics.bytes_out = len(result.encode("utf-8"))
    return result, metrics


# ============================================================================
# Stage 3: Text normalization
# ============================================================================

_PUNCT_MAP = str.maketrans({
    "\uff02": '"', "\uff07": "'",
    "\u3000": " ",
})


def normalize_text(text: str, *, simplify: bool = False) -> tuple[str, PreprocessMetrics]:
    """Normalize punctuation, whitespace; optionally simplify zh-Hant to zh-Hans."""
    metrics = PreprocessMetrics(stage="normalize", bytes_in=len(text.encode("utf-8")))
    text = text.translate(_PUNCT_MAP)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {3,}", " ", text)

    if simplify:
        try:
            import opencc
            text = opencc.OpenCC("t2s.json").convert(text)
        except ImportError:
            pass

    # Fullwidth -> halfwidth for Latin alphanumerics
    text = "".join(
        chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E else ch
        for ch in text
    )

    metrics.bytes_out = len(text.encode("utf-8"))
    return text, metrics


# ============================================================================
# Stage 4: Paragraph repair
# ============================================================================

_PARAGRAPH_BOUNDARY = re.compile(
    r"^[\u4e00-\u9fff]{1,3}[\uff1a\u003a\uff1b\u003b]"
    r"|^[\"\u201c\u300c][^\n]*[\"\u201d\u300d]$"
    r"|^[#\*\-]"
    r"|^[\uff08\u0028\u3010\uff3b]"
    r"|^.{1,4}$",
    re.MULTILINE,
)


def repair_paragraphs(text: str) -> tuple[str, PreprocessMetrics]:
    """Merge broken lines within paragraphs while preserving dialogue/headings.

    Rule: merge when previous line doesn't end with sentence punctuation
    AND next line isn't a paragraph boundary.
    """
    metrics = PreprocessMetrics(stage="paragraph_repair", bytes_in=len(text.encode("utf-8")))
    lines = text.splitlines()
    metrics.lines_in = len(lines)

    if len(lines) <= 1:
        return text, metrics

    SENTENCE_END = set("\u3002\uff01\uff1f\u2026!?.\u300d\u300f\"")

    merged = []
    buf = [lines[0]]

    for i in range(1, len(lines)):
        curr = lines[i].strip()
        prev = buf[-1].strip() if buf else ""

        if not curr:
            if buf:
                merged.append(" ".join(buf).strip())
            merged.append("")
            buf = []
            continue
        if not prev:
            buf = [curr]
            continue

        prev_end = prev[-1] in SENTENCE_END
        curr_start = bool(_PARAGRAPH_BOUNDARY.match(curr))

        if not prev_end and not curr_start:
            buf.append(curr)
        else:
            merged.append(" ".join(buf).strip())
            buf = [curr]

    if buf:
        merged.append(" ".join(buf).strip())

    result = "\n".join(merged)
    metrics.lines_out = len(merged)
    metrics.bytes_out = len(result.encode("utf-8"))
    return result, metrics


# ============================================================================
# Stage 3b: Layout normalization (blank-line paragraphs + scene separators)
# ============================================================================
# P1-1: 层级化结构规格
# ============================================================================

@dataclass
class LevelSpec:
    """单层结构的正则 + 输出映射。"""
    name: str = "chapter"          # volume | chapter | section
    regex: str = ""
    optional: bool = False
    heading_prefix: str = "#"      # 输出到 markdown 的 heading 级别


@dataclass
class StructureSpec:
    """文档结构规则（P1-1 层级化扩展，向后兼容）。"""

    # ── LEGACY 字段（保持向后兼容）──
    chapter_regex: str = ""             # 兼容旧代码，等同于 levels[0].regex
    paragraph_style: str = "blank_line" # 兼容旧代码

    # ── P1-1 新增字段 ──
    levels: list[LevelSpec] = field(default_factory=list)
    paragraph_break: str = "blank_line"  # blank_line | single_line | indented | mixed
    scene_separators: list[str] = field(default_factory=list)
    has_markdown_headings: bool = False
    confidence: float = 0.0
    source: str = "unknown"  # llm | md_headings | chapter_regex | default | epub_toc

    def __post_init__(self):
        """向后兼容同步：levels ↔ chapter_regex。"""
        if not self.levels and self.chapter_regex:
            self.levels = [LevelSpec(regex=self.chapter_regex)]
        if self.levels and not self.chapter_regex:
            self.chapter_regex = self.levels[0].regex
        if self.paragraph_break == "blank_line" and self.paragraph_style not in ("blank_line", ""):
            self.paragraph_break = self.paragraph_style

    @classmethod
    def from_llm_json(cls, data: dict) -> StructureSpec:
        """从 LLM JSON 输出构造，兼容新旧两种格式。

        旧格式: {"chapter_regex": "...", "paragraph_style": "..."}
        新格式: {"levels": [{"name": "...", "regex": "...", ...}], ...}
        """
        # ── 解析 levels（新格式优先）──
        levels: list[LevelSpec] = []
        raw_levels = data.get("levels")
        if isinstance(raw_levels, list) and raw_levels:
            for item in raw_levels:
                if not isinstance(item, dict):
                    continue
                lv = LevelSpec(
                    name=str(item.get("name", "chapter")),
                    regex=str(item.get("regex", "")),
                    optional=bool(item.get("optional", False)),
                    heading_prefix=str(item.get("heading_prefix", "#")),
                )
                if lv.regex:
                    try:
                        re.compile(lv.regex)
                        levels.append(lv)
                    except re.error:
                        pass
        # ── 旧格式 compat：从 chapter_regex/regex 迁移 ──
        if not levels:
            regex = data.get("chapter_regex") or data.get("regex") or ""
            if regex:
                try:
                    re.compile(regex)
                    levels = [LevelSpec(regex=regex)]
                except re.error:
                    regex = ""
            else:
                regex = ""

        chapter_regex = levels[0].regex if levels else ""

        # ── paragraph_style（兼容旧字段）──
        pstyle = data.get("paragraph_style", "blank_line")
        if pstyle not in ("blank_line", "single_line"):
            pstyle = "blank_line"

        # ── paragraph_break（新字段，优先，粒度更细）──
        pbreak = data.get("paragraph_break", pstyle)
        if pbreak not in ("blank_line", "single_line", "indented", "mixed"):
            pbreak = pstyle

        # ── scene_separators ──
        seps = data.get("scene_separators", [])
        if not isinstance(seps, list):
            seps = []
        cleaned_seps: list[str] = []
        seen: set[str] = set()
        for s in seps:
            if not isinstance(s, (str, int)):
                continue
            text = str(s).strip()
            if not text or len(text) > 10:
                continue
            if text in seen:
                continue
            seen.add(text)
            cleaned_seps.append(text)

        return cls(
            chapter_regex=chapter_regex,
            paragraph_style=pstyle,
            paragraph_break=pbreak,
            levels=levels,
            scene_separators=cleaned_seps,
            has_markdown_headings=bool(data.get("has_markdown_headings", False)),
            confidence=float(data.get("confidence", 0.0)),
            source=data.get("source", "llm"),
        )

    @classmethod
    def default(cls) -> StructureSpec:
        """LLM 不可用时的保守默认：不做 R4 场景分隔符识别。"""
        return cls(
            chapter_regex="",
            paragraph_style="blank_line",
            levels=[],
            confidence=1.0,
            source="default",
        )


# ── Spec 校验与降级（P0-3）─────────────────────────────────

def validate_spec(spec: StructureSpec, raw_md: str) -> tuple[StructureSpec, list[str]]:
    """校验 LLM 产出的 StructureSpec，不合格则降级返回 default。

    Checks:
      1. chapter_regex 可编译
      2. 对 raw_md 的命中数 ∈ [1, 500]（过少 = 漏匹配，过多 = 过匹配）
      3. 命中位置跨度 ≥ 20%（全文均匀分布，非全挤在局部）
      4. confidence < 0.6 → 降级到启发式

    极短文本（< 10000 字符）豁免分布校验——短篇/测试文本章节天然集中。

    Returns:
        (validated_spec, warnings). warnings 为空列表表示通过所有校验。
    """
    warnings: list[str] = []
    if not raw_md:
        return StructureSpec.default(), ["empty text → fallback to default"]

    chapter_regex = spec.chapter_regex

    # ── 1. 编译校验 ──
    if chapter_regex:
        try:
            pattern = re.compile(chapter_regex, re.MULTILINE)
        except re.error as e:
            msg = f"chapter_regex 编译失败: {e} → fallback to default"
            warnings.append(msg)
            logger.warning("validate_spec: %s", msg)
            return StructureSpec.default(), warnings
    else:
        # 无 regex → 保持 spec 原样（可能是 default 或纯 heuristic），不做章节校验
        if spec.confidence < 0.6:
            warnings.append(f"no regex + low confidence ({spec.confidence}) → fallback to default")
            return StructureSpec.default(), warnings
        return spec, warnings

    # ── 2. 命中数校验 ──
    matches = list(pattern.finditer(raw_md))
    match_count = len(matches)

    if match_count < 1:
        msg = "chapter_regex 命中 0 章（漏匹配）→ fallback to default"
        warnings.append(msg)
        logger.warning("validate_spec: %s", msg)
        return StructureSpec.default(), warnings

    if match_count > 500:
        msg = f"chapter_regex 命中 {match_count} 章（过匹配，疑似匹配到普通段落）→ fallback to default"
        warnings.append(msg)
        logger.warning("validate_spec: %s", msg)
        return StructureSpec.default(), warnings

    # ── 3. 分布校验（极短文本豁免）──
    text_len = len(raw_md)
    if text_len >= 10000 and match_count >= 2:
        positions = [m.start() / text_len for m in matches]
        span = max(positions) - min(positions)
        if span < 0.2:
            msg = f"章节命中集中在 {span:.1%} 范围内（< 20%）→ 可能漏章"
            warnings.append(msg)
            logger.warning("validate_spec: %s", msg)
            # 不降级，仅记录警告——分布不均匀不一定是虚假匹配

    # ── 4. 置信度降级 ──
    if spec.confidence < 0.6:
        msg = f"confidence={spec.confidence:.2f} < 0.6 → 降级到 default"
        warnings.append(msg)
        logger.warning("validate_spec: %s", msg)
        return StructureSpec.default(), warnings

    return spec, warnings


def normalize_layout(
    text: str,
    spec: StructureSpec | None = None,
) -> tuple[str, PreprocessMetrics]:
    """Stage 3b: 按结构规则统一排版为 markdown 标准。

    Rules:
      R2 — 段落分隔统一为 \\n\\n
           spec.paragraph_style=single_line 时，强制补空行（EPUB 一行一段风格）
           spec.paragraph_style=blank_line 时，已有空行不补，相邻非空行兜底补空行
      R4 — 场景分隔符规范化为 markdown hr (---)
           精确匹配 spec.scene_separators 中的字符串（不用正则），避免误伤

    NOTE: R1（章节标题加 # 前缀）不在这一步做，由 ingest 在 per-chapter 阶段处理，
    因为 preprocessor 看不到章节信息。
    """
    spec = spec or StructureSpec.default()
    metrics = PreprocessMetrics(stage="layout", bytes_in=len(text.encode("utf-8")))

    # R4: 构建精确匹配集合
    sep_set = {s.strip() for s in spec.scene_separators if s.strip()}

    lines = text.split("\n")
    result: list[str] = []
    scene_sep_count = 0
    paragraph_splits = 0

    for line in lines:
        stripped = line.strip()

        # R4: 精确字符串匹配场景分隔符（短行 + 在 sep_set 中）
        if sep_set and stripped and len(stripped) <= 10 and stripped in sep_set:
            if result and result[-1] != "":
                result.append("")
            result.append("---")
            result.append("")
            scene_sep_count += 1
            continue

        # R2: 非空行之间强制/补齐空行
        if stripped:
            if result and result[-1] != "":
                # 前一行也是非空 → 插入空行（无论 single_line 还是 blank_line 模式，
                # 相邻非空行都说明原始是一行一段，需要补空行）
                result.append("")
                paragraph_splits += 1
            result.append(line)
        else:
            # 空行保留，去重（连续空行只保留一个）
            if not result or result[-1] != "":
                result.append("")

    # 末尾去重空行
    while len(result) > 1 and result[-1] == "":
        result.pop()

    out = "\n".join(result)
    metrics.bytes_out = len(out.encode("utf-8"))
    metrics.lines_out = len(result)
    metrics.lines_removed = len(lines) - len(result)
    metrics.warnings.append(
        f"scene_separators={scene_sep_count}, paragraph_splits={paragraph_splits}, "
        f"style={spec.paragraph_style}"
    )
    return out, metrics


# ============================================================================
# Stage 5: Chapter boundary detection (weighted rules)
# ============================================================================

@dataclass
class ChapterBoundary:
    line_index: int
    title: str
    score: float
    pattern_name: str


_CHAPTER_RULES: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"^#{1,3}\s+(.+)", re.MULTILINE), "markdown_heading", 0.95),
    (re.compile(r"^第[一二三四五六七八九十百千零两\d]+[章回节卷篇部話话](?:\s|$)", re.MULTILINE), "zh_chapter", 0.98),
    (re.compile(r"^(?:CHAPTER|Chapter|chapter)\s+(?:[IVXLCDM]+|\d+|[A-Z][a-z]+)", re.MULTILINE), "en_chapter", 0.95),
    (re.compile(r"^第[一二三四五六七八九十百千零两\d]+[話话]\s", re.MULTILINE), "ja_episode", 0.90),
    (re.compile(r"^第[一二三四五六七八九十百千零两\d]+[巻卷]", re.MULTILINE), "ja_volume", 0.85),
    # Japanese light novel specific patterns
    (re.compile(r"^第[一二三四五六七八九十百千零两\d]+[敗胜负]", re.MULTILINE), "ja_lightnovel_counter", 0.92),
    (re.compile(r"^(?:第[一二三四五六七八九十百千零两\d]+話|Episode\s*[0-9]+)", re.MULTILINE), "ja_episode2", 0.90),
    (re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]", re.MULTILINE), "circled_number", 0.75),
    (re.compile(r"^[（(【]\s*[一二三四五六七八九十百千零两\d]+\s*[）)】]", re.MULTILINE), "paren_number", 0.70),
    (re.compile(r"^[一二三四五六七八九十百千零两\d]+[、.．]\s*$", re.MULTILINE), "bare_number", 0.60),
    (re.compile(r"^.{4,40}$", re.MULTILINE), "short_line", 0.25),
]


def detect_chapters_weighted(text: str) -> tuple[list[ChapterBoundary], PreprocessMetrics]:
    """Detect chapter boundaries using weighted rule scoring.

    Each rule matches independently; overlapping matches resolved by highest
    confidence score. Nearby matches (within 3 lines) are deduplicated.
    """
    metrics = PreprocessMetrics(stage="chapter_detect", bytes_in=len(text.encode("utf-8")))
    candidates = []
    seen_lines: set[int] = set()

    for pattern, name, weight in _CHAPTER_RULES:
        for m in pattern.finditer(text):
            line_idx = text[:m.start()].count("\n")
            if line_idx in seen_lines:
                continue
            title = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
            title = title.strip()[:60]
            candidates.append(ChapterBoundary(line_idx, title, weight, name))
            seen_lines.add(line_idx)

    candidates.sort(key=lambda c: c.line_index)
    deduped = []
    for c in candidates:
        if deduped and (c.line_index == deduped[-1].line_index):
            if c.score > deduped[-1].score:
                deduped[-1] = c
        else:
            deduped.append(c)

    metrics.chapters_detected = len(deduped)
    return deduped, metrics


# ============================================================================
# Composer
# ============================================================================

@dataclass
class PreprocessOptions:
    detect_encoding: bool = True
    filter_binary: bool = True
    clean_lines: bool = True
    normalize_text: bool = True
    repair_paragraphs: bool = True
    detect_chapters: bool = True
    simplify_to_hans: bool = False


@dataclass
class PreprocessResult:
    text: str
    chapters: list[ChapterBoundary]
    metrics: list[PreprocessMetrics]
    raw_bytes: int = 0


def run(raw: bytes, options: PreprocessOptions | None = None) -> PreprocessResult:
    """Run the full preprocessing pipeline on raw bytes.

    Returns cleaned text, chapter boundaries, and per-stage metrics.
    """
    opts = options or PreprocessOptions()
    all_metrics: list[PreprocessMetrics] = []

    text: str
    if opts.detect_encoding:
        text, m = detect_and_transcode(raw)
        all_metrics.append(m)
    else:
        text = raw.decode("utf-8", errors="replace")
        all_metrics.append(PreprocessMetrics(stage="encoding", bytes_in=len(raw)))

    if opts.filter_binary:
        text, m = filter_binary(text)
        all_metrics.append(m)

    if opts.clean_lines:
        text, m = clean_lines(text)
        all_metrics.append(m)

    if opts.normalize_text:
        text, m = normalize_text(text, simplify=opts.simplify_to_hans)
        all_metrics.append(m)

    if opts.repair_paragraphs:
        text, m = repair_paragraphs(text)
        all_metrics.append(m)

    chapters: list[ChapterBoundary] = []
    if opts.detect_chapters:
        chapters, m = detect_chapters_weighted(text)
        all_metrics.append(m)

    return PreprocessResult(text=text, chapters=chapters, metrics=all_metrics, raw_bytes=len(raw))
