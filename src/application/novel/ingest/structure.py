"""Ingest Phase 2 — chapter structure parsing (regex → LLM → per-chapter repair).

Extracted from the former monolithic ``ingest.py``; logic unchanged.
"""

from __future__ import annotations

import logging
import re

from src.application.novel.ingest.convert import _build_shared_llm
from src.domain.novel.preprocessor import StructureSpec

logger = logging.getLogger("agent")


_PRE_SCAN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("中文 第N章/回/节/卷", re.compile(r"^第[一二三四五六七八九十百千零两\d]+[章回节卷篇部]", re.MULTILINE)),
    ("日文 第N話/敗/勝/巻", re.compile(r"^第[一二三四五六七八九十百千零\d]+[話敗勝巻]", re.MULTILINE)),
    ("英文 Chapter N", re.compile(r"^(?:Chapter|CHAPTER)\s+(?:[IVXLCDM]+|\d+)", re.MULTILINE)),
    ("Markdown 标题", re.compile(r"^#{1,3}\s+", re.MULTILINE)),
    ("装饰型 ～第N话～", re.compile(r"^[～~◆◇▲■○●].*第[一二三四五六七八九十百千零\d]+[話話章回].*[～~◆◇▲■○●]", re.MULTILINE)),
    ("编号 一、/1./(一)", re.compile(r"^[（(【]?\s*[一二三四五六七八九十百千零两\d]+\s*[）)】]?\s*[、.．]", re.MULTILINE)),
]


def _pre_scan_regex_hits(raw_md: str) -> str:
    """用已知章节模式预扫描 raw_md，返回人类可读的统计摘要。

    帮助 LLM 在不看全文的情况下了解全文章节格式线索。
    返回空字符串表示预扫描未发现任何已知模式。
    """
    lines: list[str] = []
    text_len = len(raw_md)
    lines.append(f"全文 {text_len} 字符")
    total_hits = 0

    for name, pattern in _PRE_SCAN_PATTERNS:
        hits = list(pattern.finditer(raw_md))
        if not hits:
            continue
        total_hits += len(hits)
        # 位置百分位
        positions = [m.start() / text_len for m in hits]
        pos_range = f"{min(positions):.0%}–{max(positions):.0%}"
        # 前 5 个匹配样例
        examples = [m.group(0).strip()[:30] for m in hits[:5]]
        lines.append(
            f"  {name}: {len(hits)} 命中, "
            f"跨度 {pos_range}, "
            f"例: {', '.join(examples)}"
        )

    if not lines[1:]:  # 只有第一行（文件大小），无命中
        return ""

    if total_hits == 0:
        return "无已知章节模式匹配 — 文本可能无结构化章节标记"

    return "\n".join(lines)


def _fast_detect_structure(raw_md: str, epub_toc: list[str] | None = None) -> StructureSpec | None:
    """快速路径：用廉价规则预判文档结构，跳过 LLM 调用。

    检测优先级（先命中先返回）：
      0. EPUB TOC：≥3 个章节标题 → confidence=0.95，跳过 LLM
      1. Markdown 标题：≥5 个 `#`/`##`/`###` 行且分布跨 30% → confidence=0.9
      2. 中文/日文章节正则：命中 3-500 个且分布跨 30% → confidence=0.7

    Returns:
        StructureSpec 若能通过快速路径判定，否则 None（需走 LLM）。
    """
    from src.domain.novel.preprocessor import StructureSpec
    text_len = len(raw_md)
    if text_len < 1000:
        return None  # 太短，不值得快速路径

    # ── Step 0: EPUB TOC（P1-2）──
    if epub_toc and len(epub_toc) >= 3:
        # Build chapter_regex from TOC title patterns
        logger.info(
            "Fast path (EPUB TOC): %d entries, skipping LLM",
            len(epub_toc),
        )
        return StructureSpec(
            chapter_regex=r"^#{1,3}\s",  # EPUB already has MD headings from _convert_epub
            paragraph_style="blank_line",
            scene_separators=[],
            has_markdown_headings=True,
            confidence=0.95,
            source="epub_toc",
        )

    # ── Step 1: Markdown 标题检测 ──
    md_pattern = re.compile(r"^#{1,3}\s", re.MULTILINE)
    md_matches = list(md_pattern.finditer(raw_md))
    if len(md_matches) >= 5:
        positions = [m.start() / text_len for m in md_matches]
        span = max(positions) - min(positions)
        if span >= 0.3:
            logger.info(
                "Fast path (MD headings): %d matches, span %.0f%%, skipping LLM",
                len(md_matches), span * 100,
            )
            return StructureSpec(
                chapter_regex=r"^#{1,3}\s",
                paragraph_style="blank_line",
                scene_separators=[],
                has_markdown_headings=True,
                confidence=0.9,
                source="md_headings",
            )

    # ── Step 2: 启发式中文章节/日文章节检测 ──
    cn_pattern = re.compile(
        r"^第[一二三四五六七八九十百千零两\d]+[章回节卷篇部話敗勝巻话]",
        re.MULTILINE,
    )
    cn_matches = list(cn_pattern.finditer(raw_md))
    if 3 <= len(cn_matches) <= 500:
        positions = [m.start() / text_len for m in cn_matches]
        span = max(positions) - min(positions)
        if span >= 0.3:
            logger.info(
                "Fast path (chapter regex): %d matches, span %.0f%%, skipping LLM",
                len(cn_matches), span * 100,
            )
            return StructureSpec(
                chapter_regex=(
                    r"^第[一二三四五六七八九十百千零两\d]+"
                    r"[章回节卷篇部話敗勝巻话]"
                ),
                paragraph_style="blank_line",
                scene_separators=[],
                confidence=0.7,
                source="chapter_regex",
            )

    return None


async def _analyze_structure_via_llm(
    raw_md: str,
    doc_id: str,
) -> tuple[StructureSpec, list | None]:
    """LLM 结构分析：首尾采样，输出 StructureSpec + 章节列表。

    合并了原 _detect_chapters_via_llm 的功能，一次调用解决：
    - 章节正则识别（原功能）
    - 段落分隔方式（新，R2 输入）
    - 场景分隔符识别（新，R4 输入）
    - markdown 标题检测（新，R1 输入）

    Cost: ~¥0.008 / call, ~3-5s (thinking disabled).
    Returns:
        (spec, chapters). chapters 在 confidence<0.7 或无有效 regex 时为 None。
    """
    import json
    import re as _re

    from src.domain.novel.chapter_prompt import ChapterPromptSpec, build_chapter_prompt
    from src.domain.novel.models import Chapter
    from src.domain.novel.preprocessor import StructureSpec

    llm_client = _build_shared_llm(temperature=0.0, max_tokens=2048, timeout=120.0)
    if llm_client is None:
        logger.info("LLM structure analysis skipped: no API key configured")
        return StructureSpec.default(), None

    spec_cfg = ChapterPromptSpec(sample_size=5000, mid_sample_size=2000)

    # P0-2: 构建分层样本（head + mid + tail）+ 预扫描统计
    text_len = len(raw_md)
    mid_start = max(0, text_len // 2 - spec_cfg.mid_sample_size // 2)
    mid_end = min(text_len, mid_start + spec_cfg.mid_sample_size)
    mid_sample = raw_md[mid_start:mid_end]
    pre_scan = _pre_scan_regex_hits(raw_md)

    prompt = build_chapter_prompt(
        head=raw_md[: spec_cfg.sample_size],
        tail=raw_md[-spec_cfg.sample_size :],
        spec=spec_cfg,
        mid=mid_sample,
        pre_scan=pre_scan,
    )

    try:
        content = await llm_client.achat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2048,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as e:
        # 免费/慢端点偶发超时：重试 1 次（退避 3s）再降级默认结构
        logger.warning("LLM structure analysis request failed: %s", e)
        try:
            import asyncio

            await asyncio.sleep(3)
            content = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=2048,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as e2:
            logger.warning("LLM structure analysis retry failed: %s", e2)
            return StructureSpec.default(), None

    # 3 策略 JSON 解析（直接 / ```json``` 代码块 / regex 字段提取）
    data = None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, _re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    if data is None:
        # 兜底：只提取 regex 字段
        m = _re.search(r'"regex"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
        if m:
            try:
                regex_str = m.group(1).encode().decode("unicode_escape")
                data = {"regex": regex_str}
            except Exception:
                pass

    if not data or not isinstance(data, dict):
        logger.warning("LLM structure analysis: could not parse response")
        return StructureSpec.default(), None

    # 构造 spec
    spec = StructureSpec.from_llm_json(data)
    logger.info(
        "LLM structure analysis: confidence=%.2f, paragraph=%s, seps=%s, md_headings=%s, regex=%s",
        spec.confidence, spec.paragraph_style, spec.scene_separators,
        spec.has_markdown_headings, spec.chapter_regex[:60] if spec.chapter_regex else "(none)",
    )

    # 置信度不足，返回 spec 但不采用 LLM 章节
    if spec.confidence < 0.7:
        logger.info("LLM structure analysis: low confidence, skipping chapter detection")
        return spec, None

    # 用 spec.chapter_regex 切章节
    chapters = None
    if spec.chapter_regex:
        try:
            pattern = _re.compile(spec.chapter_regex, _re.MULTILINE)
        except _re.error as e:
            logger.warning("LLM chapter_regex compile failed: %s", e)
        else:
            matches = list(pattern.finditer(raw_md))
            # Sanity check: 2-200 个匹配（防止正则太松）
            if 2 <= len(matches) <= 200:
                chapters = []
                for i, m in enumerate(matches):
                    title = m.group(0).strip()
                    start = m.end()
                    end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_md)
                    body = raw_md[start:end].strip()
                    if not body:
                        continue
                    chapters.append(Chapter(
                        chapter_id=f"{doc_id}_ch_{i}",
                        title=title,
                        order=i,
                        text=body,
                    ))
                if len(chapters) < 2:
                    chapters = None
                else:
                    logger.info("LLM structure analysis: %d chapters", len(chapters))
            else:
                logger.info(
                    "LLM structure analysis: %d matches (out of 2-200 range), skipping",
                    len(matches),
                )

    return spec, chapters


async def _detect_chapters_via_llm(raw_md: str, doc_id: str) -> list | None:
    """Compatibility wrapper for the former chapter-only LLM detector.

    Delegates to ``_analyze_structure_via_llm`` and returns the chapter list
    (or ``None`` / ``[]`` when structure analysis skips chapters).
    """
    _spec, chapters = await _analyze_structure_via_llm(raw_md, doc_id)
    return chapters or []


async def _parse_structure(
    raw_md: str, doc_id: str, mime_type: str,
    epub_toc: list[str] | None = None,
):
    """Phase 2 + 2b + 2c: parse chapters with LLM structure analysis.

    Pipeline:
      Phase 2  — NovelParser regex (fast, free, 5 patterns) — 兜底
      Phase 2b — LLM 结构分析 (¥0.008, ~3-5s) — 所有路径都调
                 产出 StructureSpec + (可选)章节列表
      Phase 2c — per-chapter: repair_paragraphs → normalize_layout(spec) → R1

    Returns:
        NovelDocument on success, None on failure (error logged).
    """
    from src.domain.novel.parser import NovelParser
    from src.domain.novel.preprocessor import (
        normalize_layout,
        repair_paragraphs,
        validate_spec,
    )

    # Phase 2: regex 快速路径（作为兜底）
    parser = NovelParser()
    document = parser.parse(raw_md, doc_id=doc_id, source_format=mime_type)
    logger.info(
        "Parsed '%s' (regex): title='%s', %d chapters, %d words",
        doc_id, document.title, document.total_chapters, document.total_words,
    )

    # Phase 2b: 快速路径预判 + LLM 结构分析（降级调用）
    spec = _fast_detect_structure(raw_md, epub_toc)
    llm_chapters = None

    if spec is not None:
        logger.info(
            "Fast path hit for '%s': source=%s, confidence=%.2f, regex=%s",
            doc_id, spec.source, spec.confidence,
            spec.chapter_regex[:50] if spec.chapter_regex else "(none)",
        )
    else:
        # 快速路径未命中 → 走完整 LLM 分析
        spec, llm_chapters = await _analyze_structure_via_llm(raw_md, doc_id)

    # ── P0-3: 校验并降级 spec ──
    spec, spec_warnings = validate_spec(spec, raw_md)
    if spec_warnings:
        for w in spec_warnings:
            logger.warning("Spec validation warning for '%s': %s", doc_id, w)
    else:
        logger.info("Spec validation passed for '%s': %.2f conf, regex=%s",
                     doc_id, spec.confidence,
                     spec.chapter_regex[:50] if spec.chapter_regex else "(none)")

    # 章节列表选择：LLM 产出的章节比 regex 多时用 LLM
    if llm_chapters and len(llm_chapters) > len(document.chapters):
        logger.info(
            "LLM chapters (%d) > regex chapters (%d), adopting LLM result",
            len(llm_chapters), len(document.chapters),
        )
        document.chapters = llm_chapters
        document.metadata["total_words"] = sum(len(ch.text) for ch in llm_chapters)
        document.metadata["total_chapters"] = len(llm_chapters)
    else:
        logger.info("Keeping regex result (%d chapters)", len(document.chapters))

    # Phase 2c: per-chapter 标准化
    # 顺序：normalize_layout(spec) → repair_paragraphs → R1 逐章判断加 # title
    for ch in document.chapters:
        try:
            # 1. normalize_layout: R2 空行分段 + R4 场景分隔符→--- (先规格化再修复)
            ch.text, _ = normalize_layout(ch.text, spec)
            # 2. repair_paragraphs: 合并破碎行（CJK 文本断行修复，spec-aware）
            ch.text, _ = repair_paragraphs(ch.text)
            # 3. R1: 逐章判断是否需要补 # title 前缀
            body = ch.text.lstrip()
            if not re.match(r"^#{1,6}\s", body):
                if ch.title:  # parser 提取到非空标题
                    ch.text = f"# {ch.title}\n\n{body}"
                # else: 无标题章（序言/楔子），不强补
        except Exception as e:
            logger.warning("post-processing failed for chapter %s: %s", ch.chapter_id, e)

    logger.info(
        "Final structure: title='%s', %d chapters, %d words",
        document.title, document.total_chapters, document.total_words,
    )
    return document
