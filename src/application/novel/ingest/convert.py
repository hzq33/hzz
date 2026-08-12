"""Ingest Phase 0/1/1b — validation, format conversion, preprocessing.

Extracted from the former monolithic ``ingest.py``; logic unchanged.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import zipfile
from pathlib import Path
from typing import Any

ProgressCallback = Any  # re-exported by package __init__

logger = logging.getLogger("agent")

# Register additional MIME types
mimetypes.add_type("application/epub+zip", ".epub")

SUPPORTED_MIMES = [
    "application/epub+zip",
    "text/plain",
    "text/markdown",
]

# Magic numbers for binary formats we don't support.
# NOTE: EPUB is intentionally absent — EPUB is a ZIP container (PK\x03\x04),
# and we must allow it through so _convert_epub can unpack it.
_BINARY_MAGIC = {
    b"%PDF": "PDF",
    b"\x89PNG": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
    b"GIF8": "GIF image",
}


def _qa_config() -> dict:
    """Load novel_rag.qa from config.yaml."""
    from pathlib import Path

    import yaml

    cfg_path = Path(__file__).resolve().parents[4] / "config.yaml"
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return dict((raw.get("novel_rag") or {}).get("qa") or {})
    except Exception as e:
        logger.debug("qa config load failed: %s", e)
        return {
            "enabled": True,
            "max_source_blocks": 30,
            "per_block": 2,
            "prefer_character_overlap": True,
        }


def _select_narrative_for_qa(
    narrative_blocks: list,
    character_names: list[str],
    *,
    max_blocks: int = 30,
    prefer_character_overlap: bool = True,
) -> list:
    """Prefer narrative chunks that mention known character names."""
    if not narrative_blocks:
        return []
    if not prefer_character_overlap or not character_names:
        return narrative_blocks[:max_blocks]

    names = [n for n in character_names if n]
    scored: list[tuple[int, int, object]] = []
    for i, b in enumerate(narrative_blocks):
        text = getattr(b, "narrative_text", "") or ""
        hit = sum(1 for n in names if n in text)
        # denser name hits first; stable by original order
        scored.append((-hit, i, b))
    scored.sort()
    return [b for _, _, b in scored[:max_blocks]]


def _local_llm_config() -> dict:
    """Load novel_rag.local_llm from config.yaml + LOCAL_LLM_ENABLED env override."""
    import os
    from pathlib import Path

    import yaml

    cfg_path = Path(__file__).resolve().parents[4] / "config.yaml"
    local: dict = {}
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        local = dict((raw.get("novel_rag") or {}).get("local_llm") or {})
    except Exception as e:
        logger.debug("local_llm config load failed: %s", e)

    env = os.getenv("LOCAL_LLM_ENABLED", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        local["enabled"] = True
    elif env in ("0", "false", "no", "off"):
        local["enabled"] = False
    return local


def _build_shared_llm(
    temperature: float = 0.1,
    max_tokens: int = 1024,
    timeout: float = 25.0,
    endpoint: str | None = None,
):
    """Build a SharedLLMClient from config for LLM-assisted ingest stages.

    Returns None if no API key is configured (stages fall back gracefully).
    Centralizes the config→client construction used by dialogue fallback and QA.

    ``endpoint``: 前端 llm-config 的调用点标识（如 "dialogue_extract"）；
    配置了则覆盖服务商/模型/key/参数（未配置项回退 config.yaml 默认）。

    ``timeout``: per-request seconds. LLM full-scan inventory (12 万字符输入 + 完整
    JSON 输出) needs more headroom than short per-chapter calls — pass 60-90 there.
    """
    try:
        from src.application.novel.factory import _load_raw_config, _substitute_env
        cfg = _load_raw_config()
        agent_cfg = cfg.get("agent", {})
        api_key = _substitute_env(agent_cfg.get("api_key", ""))
        base_url = _substitute_env(agent_cfg.get("base_url", ""))
        model = agent_cfg.get("model", "deepseek-v4-flash")
        fallback_model = _substitute_env(agent_cfg.get("fallback_model", ""))

        # 前端 llm-config 覆盖（endpoint 指定时）
        if endpoint:
            try:
                from src.shared.llm_config import get_endpoint_config

                ep = get_endpoint_config(endpoint)
                if not ep.get("enabled", True):
                    return None
                if ep.get("api_key"):
                    api_key = ep["api_key"]
                if ep.get("base_url"):
                    base_url = ep["base_url"]
                if ep.get("model"):
                    model = ep["model"]
                if ep.get("temperature") is not None:
                    temperature = float(ep["temperature"])
                if ep.get("max_tokens") is not None:
                    max_tokens = int(ep["max_tokens"])
                # endpoint 覆盖 = 换服务商（如 glm）→ fallback 模型（deepseek-v4-pro）
                # 与当前 base_url 不匹配，发到 GLM 端点必 400 → 禁用 fallback
                fallback_model = ""
            except Exception:  # noqa: BLE001
                pass

        if not api_key:
            return None
        base_url = base_url or "https://api.deepseek.com"

        thinking_disabled = None  # None → auto（DeepSeek 自动禁用）
        if endpoint:
            try:
                from src.shared.llm_config import get_endpoint_config as _ge

                _ep = _ge(endpoint)
                _th = str(_ep.get("thinking") or "auto").strip().lower()
                if _th == "off":
                    thinking_disabled = True
                elif _th == "on":
                    thinking_disabled = False
            except Exception:  # noqa: BLE001
                pass

        from src.shared.llm import SharedLLMClient
        primary = {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
        }
        fallback = None
        if fallback_model:
            fallback = {
                "base_url": base_url,
                "api_key": api_key,
                "model": fallback_model,
            }
        return SharedLLMClient(
            primary=primary,
            fallback=fallback,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            thinking_disabled=thinking_disabled,
        )
    except Exception as e:
        logger.warning("Failed to build LLM client: %s", e)
        return None


def _validate_upload(file_bytes: bytes, filename: str) -> tuple[str | None, str | None]:
    """Phase 0: validate uploaded file and detect MIME type.

    Order of checks (matters):
      1. Extension whitelist (.epub/.txt/.md only) — requirement A-08
      2. MIME by extension (.epub/.txt/.md recognized first)
      3. Magic-number rejection for non-EPUB binaries (PDF/PNG/JPG/GIF)
      4. ZIP-magic rejection only if not .epub (catches DOCX/real ZIP)
      5. MIME whitelist check

    Returns:
        (mime_type, None) on success.
        (mime_type_or_None, error_message) on rejection.
    """
    # 扩展名白名单（A-08）：非 EPUB/TXT/MD 一律拒绝，不做内容兜底——
    # 旧实现会接受任何可 UTF-8 解码的内容（如 .exe 的 "MZ..." 头）。
    suffix = Path(filename).suffix.lower()
    if suffix not in {".epub", ".txt", ".md"}:
        return None, f"Unsupported format: {filename}. Supported: .epub, .txt, .md"

    mime_type, _ = mimetypes.guess_type(filename)

    # EPUB recognized by extension — allow through even though it's a ZIP.
    if mime_type == "application/epub+zip":
        return mime_type, None

    # Reject known binary formats by magic number.
    magic = file_bytes[:4]
    if magic in _BINARY_MAGIC:
        name = _BINARY_MAGIC[magic]
        return None, f"{name} files are not supported. Convert to TXT/MD/EPUB first."

    # ZIP magic that isn't EPUB (e.g. DOCX, plain ZIP) — reject.
    if magic == b"PK\x03\x04":
        return None, "ZIP/DOCX files are not supported. Convert to TXT/MD/EPUB first."

    # Whitelist check.
    if mime_type in SUPPORTED_MIMES:
        return mime_type, None

    return mime_type, f"Unsupported format: {filename}. Supported: .epub, .txt, .md"


def preprocess_raw_md(raw_md: str) -> str:
    """公开入口：Phase 1b 清洗（filter_binary → clean_lines → normalize）。

    dev 脚本（analysis/diagnostics/verify）复刻 ingest 清洗流程时使用，
    不直接 import 私有实现。
    """
    return _preprocess_raw_md(raw_md)


def convert_to_md(file_bytes: bytes, filename: str, mime_type: str) -> tuple[str, list[str] | None]:
    """公开入口：Phase 1 文件 → raw Markdown（MIME 分发）。

    dev 脚本验证 ingest 转换链路时使用，不直接 import 私有实现。
    """
    return _convert_to_md(file_bytes, filename, mime_type)


def _preprocess_raw_md(raw_md: str) -> str:
    """Phase 1b: apply binary filter, line cleaning, and normalization.

    Three stages run in sequence:
      1. filter_binary  — remove binary garbled lines
      2. clean_lines    — score-based ad/junk line removal (min_score=0.3)
      3. normalize_text — punctuation half/full-width, whitespace collapse

    NOTE: repair_paragraphs is intentionally deferred to Phase 2c (per-chapter)
    to avoid merging chapter heading lines into adjacent paragraphs before
    chapter detection runs. See parser.check_completeness.
    """
    from src.domain.novel.preprocessor import (
        clean_lines,
        filter_binary,
        normalize_text,
    )
    for stage_fn, stage_name in [
        (filter_binary, "binary_filter"),
        (clean_lines, "line_clean"),
        (normalize_text, "normalize"),
    ]:
        try:
            raw_md, _ = stage_fn(raw_md)
        except Exception as e:
            logger.warning("Preprocessor stage '%s' failed (skipped): %s", stage_name, e)
    logger.info(
        "Preprocessor: applied binary + line clean + normalize "
        "(paragraph repair deferred to per-chapter)"
    )
    return raw_md


def _convert_to_md(file_bytes: bytes, filename: str, mime_type: str) -> tuple[str, list[str] | None]:
    """Phase 1: convert uploaded file to raw Markdown based on MIME type.

    MIME dispatch:
      - application/epub+zip → _convert_epub (OPF spine + HTML parsing) + TOC
      - text/plain | text/markdown → detect_and_transcode (GBK/Shift-JIS → UTF-8)

    Returns (raw_md, epub_toc). epub_toc is None for non-EPUB files.
    Other MIME types never reach here — _validate_upload rejects them in Phase 0.
    """
    if mime_type == "application/epub+zip":
        return _convert_epub(file_bytes, filename)
    # text/plain or text/markdown
    from src.domain.novel.preprocessor import detect_and_transcode
    text, _ = detect_and_transcode(file_bytes)
    return text, None




def convert_epub(file_bytes: bytes, filename: str) -> tuple[str, list[str] | None]:
    """公开入口：epub → raw Markdown + TOC（dev 脚本验证用）。"""
    return _convert_epub(file_bytes, filename)


def _convert_epub(file_bytes: bytes, filename: str) -> tuple[str, list[str] | None]:
    """Convert EPUB to Markdown, preserving chapter structure.

    Improvements over naive text extraction:
    - Reads OPF spine for correct reading order (not filename sort)
    - Skips non-content files (nav.xhtml, toc.ncx, cover, copyright)
    - Extracts h1-h3 headings as Markdown headings, not plain text
    - Joins CJK text without spaces (preserves intra-line breaks)
    - Outputs `# 章节标题\\n\\n正文` for downstream NovelParser
    - P1-2: Extracts EPUB TOC (chapter title list) for fast-path structure detection

    Returns (raw_md, epub_toc). epub_toc is None if TOC extraction fails.
    """
    import io
    import re
    import zipfile
    from html.parser import HTMLParser
    # Prefer defusedxml for untrusted XML (uploaded epub OPF) to block
    # entity-expansion / XXE attacks; fall back to stdlib only if unavailable.
    try:
        from defusedxml import ElementTree as ET
    except ImportError:  # pragma: no cover - defusedxml is in requirements
        from xml.etree import ElementTree as ET

    # Tags whose content should be skipped entirely.
    # h1 is included here because the chapter title is extracted separately
    # by _extract_chapter_title and prepended as `# title` by the outer loop;
    # emitting h1 text as body would duplicate the title in the chapter text.
    _SKIP_TAGS = frozenset(('script', 'style', 'head', 'nav', 'header', 'footer', 'h1'))
    # Block-level tags that force a line break after
    _BLOCK_TAGS = frozenset(
        ('p', 'div', 'section', 'article', 'br', 'li', 'tr',
         'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre')
    )
    # Heading tags to preserve as Markdown sub-headings (h1 is dropped, not
    # emitted — see _SKIP_TAGS).
    _HEADING_TAGS = {'h2': '##', 'h3': '###', 'h4': '####'}
    # Filenames that are non-content (navigation/metadata)
    _NON_CONTENT_PATTERNS = (
        re.compile(r'nav\.x?html?$', re.IGNORECASE),
        re.compile(r'toc\.x?html?$', re.IGNORECASE),
        re.compile(r'cover\.x?html?$', re.IGNORECASE),
        re.compile(r'copyright\.x?html?$', re.IGNORECASE),
        re.compile(r'colophon\.x?html?$', re.IGNORECASE),
        re.compile(r'titlepage\.x?html?$', re.IGNORECASE),
        re.compile(r'contents?\.x?html?$', re.IGNORECASE),
        re.compile(r'index\.x?html?$', re.IGNORECASE),
    )
    # Chapters with body shorter than this are treated as metadata
    # (colophon, credits, illustrations list) and dropped.
    _MIN_CHAPTER_CHARS = 100

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self._skip_depth = 0
            self._heading_level: str | None = None
            self._heading_buf: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag in _SKIP_TAGS:
                self._skip_depth += 1
                return
            if self._skip_depth:
                return
            # <hr> → markdown 水平线（场景分隔符或章节装饰）
            if tag == 'hr':
                self.parts.append('\n\n---\n\n')
                return
            if tag in _HEADING_TAGS:
                self._heading_level = _HEADING_TAGS[tag]
                self._heading_buf = []

        def handle_endtag(self, tag):
            if tag in _SKIP_TAGS and self._skip_depth:
                self._skip_depth -= 1
                return
            if self._skip_depth:
                return
            # Flush heading buffer as Markdown heading
            if tag in _HEADING_TAGS and self._heading_level:
                heading_text = ''.join(self._heading_buf).strip()
                if heading_text:
                    self.parts.append(f"\n\n{self._heading_level} {heading_text}\n\n")
                self._heading_level = None
                self._heading_buf = []
            if tag in _BLOCK_TAGS:
                self.parts.append('\n')

        def handle_data(self, data):
            if self._skip_depth:
                return
            if self._heading_level:
                self._heading_buf.append(data)
                return
            t = data.strip()
            if not t:
                return
            # Insert a separating space only between two Latin/digit
            # fragments (e.g. inline <b> + text). CJK runs must NOT get
            # spurious spaces — they break word boundaries downstream.
            if self.parts:
                last = self.parts[-1]
                if last and not last[-1].isspace():
                    if _is_ascii_word_char(last[-1]) and _is_ascii_word_char(t[0]):
                        self.parts.append(' ')
            self.parts.append(t)

    def _is_content_file(path: str) -> bool:
        """Filter out navigation/cover/copyright files."""
        basename = path.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
        for pattern in _NON_CONTENT_PATTERNS:
            if pattern.search(basename):
                return False
        return True

    def _read_opf_spine(zf: zipfile.ZipFile) -> tuple[list[str], str | None]:
        """Read OPF spine for correct reading order.

        Returns (ordered_html_paths, opf_dir). Falls back to (sorted_paths, None)
        if OPF cannot be parsed.
        """
        names = zf.namelist()
        # Find OPF file from META-INF/container.xml
        opf_path = None
        try:
            container = zf.read('META-INF/container.xml').decode('utf-8', errors='replace')
            m = re.search(r'full-path=["\']([^"\']+\.opf)["\']', container)
            if m:
                opf_path = m.group(1)
        except (KeyError, Exception):
            pass

        if not opf_path or opf_path not in names:
            # Fallback: find any .opf file
            opfs = [n for n in names if n.endswith('.opf')]
            if opfs:
                opf_path = opfs[0]
            else:
                # No OPF; fallback to filename sort
                html_files = sorted(
                    [n for n in names if n.endswith(('.html', '.xhtml', '.htm'))],
                    key=_chapter_sort_key,
                )
                return html_files, None

        opf_dir = opf_path.rsplit('/', 1)[0] if '/' in opf_path else ''
        try:
            opf_xml = zf.read(opf_path).decode('utf-8', errors='replace')
            root = ET.fromstring(opf_xml)
            # Strip XML namespaces for easy querying
            for elem in root.iter():
                if '}' in elem.tag:
                    elem.tag = elem.tag.split('}', 1)[1]
            # Build manifest id->href map
            manifest: dict[str, str] = {}
            for item in root.iter('item'):
                item_id = item.get('id', '')
                href = item.get('href', '')
                media_type = item.get('media-type', '')
                if media_type in ('application/xhtml+xml', 'text/html'):
                    manifest[item_id] = href
            # Read spine order
            ordered: list[str] = []
            spine = root.find('spine')
            if spine is not None:
                for itemref in spine.iter('itemref'):
                    idref = itemref.get('idref', '')
                    if idref in manifest:
                        href = manifest[idref]
                        # Resolve relative to OPF directory
                        if opf_dir:
                            full_path = f"{opf_dir}/{href}"
                        else:
                            full_path = href
                        # Normalize path
                        full_path = full_path.replace('\\', '/')
                        ordered.append(full_path)
            if ordered:
                return ordered, opf_dir
        except Exception:
            pass

        # Final fallback: filename sort
        html_files = sorted(
            [n for n in names if n.endswith(('.html', '.xhtml', '.htm'))],
            key=_chapter_sort_key,
        )
        return html_files, None

    def _resolve_path(zf: zipfile.ZipFile, path: str, opf_dir: str | None) -> str | None:
        """Resolve a path from OPF spine to an actual zip member.

        Tries: path as-is, path relative to OPF dir, case-insensitive match.
        """
        names_set = set(zf.namelist())
        candidates = [path]
        if opf_dir:
            candidates.append(f"{opf_dir}/{path}")
        for c in candidates:
            if c in names_set:
                return c
        # Case-insensitive fallback
        lower_map = {n.lower(): n for n in names_set}
        for c in candidates:
            if c.lower() in lower_map:
                return lower_map[c.lower()]
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile:
        raise ValueError("Not a valid EPUB/ZIP file")

    # P1-2: Extract TOC before processing content
    epub_toc = _extract_epub_toc(zf)

    try:
        spine_paths, opf_dir = _read_opf_spine(zf)
        # Filter out non-content files
        content_paths = [p for p in spine_paths if _is_content_file(p)]
        # Resolve to actual zip members, dedup
        resolved: list[str] = []
        seen: set[str] = set()
        for p in content_paths:
            actual = _resolve_path(zf, p, opf_dir)
            if actual and actual not in seen:
                # Verify file exists and is HTML
                if actual.endswith(('.html', '.xhtml', '.htm')):
                    resolved.append(actual)
                    seen.add(actual)

        if not resolved:
            # Last-resort fallback: read any txt file
            text_files = [n for n in zf.namelist() if n.endswith('.txt')]
            if text_files:
                return zf.read(text_files[0]).decode('utf-8', errors='replace'), epub_toc
            raise ValueError("No readable content found in EPUB")

        parts: list[str] = []
        dropped = 0
        for i, path in enumerate(resolved):
            content = zf.read(path).decode('utf-8', errors='replace')
            extractor = _TextExtractor()
            try:
                extractor.feed(content)
            except Exception:
                # Malformed HTML; fallback to regex strip
                chapter_text = _strip_tags(content).strip()
                if not chapter_text or len(chapter_text) < _MIN_CHAPTER_CHARS:
                    dropped += 1
                    continue
                parts.append(chapter_text)
                continue
            chapter_text = ''.join(extractor.parts).strip()
            # Collapse multiple blank lines
            chapter_text = re.sub(r'\n{3,}', '\n\n', chapter_text)

            if not chapter_text or len(chapter_text) < _MIN_CHAPTER_CHARS:
                dropped += 1
                continue

            # Determine chapter title (h1 already excluded from extractor output,
            # so we prepend it here without duplication)
            chap_title = _extract_chapter_title(content, path)
            if not chap_title:
                chap_title = f"第{i+1}章"

            # 元数据章过滤：制作信息/简介/彩页/角色介绍/前言/目录 等非正文章节直接丢弃
            if _is_metadata_chapter(chap_title, chapter_text):
                dropped += 1
                continue

            parts.append(f"# {chap_title}\n\n{chapter_text}")

        if dropped:
            logger.info("EPUB conversion: dropped %d metadata/short files (<%d chars)",
                        dropped, _MIN_CHAPTER_CHARS)

        if not parts:
            raise ValueError("No readable content found in EPUB after filtering")

        return '\n\n'.join(parts), epub_toc
    finally:
        zf.close()




def _chapter_sort_key(path: str) -> tuple:
    """Sort chapter files naturally (chapter1, chapter2, ...)."""
    import re
    parts = re.split(r'(\d+)', path.lower())
    key = []
    for p in parts:
        if p.isdigit():
            key.append((0, int(p)))
        else:
            key.append((1, p))
    return tuple(key)



def _extract_epub_toc(zf: zipfile.ZipFile) -> list[str] | None:
    """P1-2: Extract chapter title list from EPUB nav.xhtml (EPUB3) or toc.ncx (EPUB2).

    Returns list of chapter titles, or None if extraction fails.
    """
    names = zf.namelist()
    # ── EPUB3: nav.xhtml ──
    nav_candidates = [n for n in names if n.lower().endswith(('nav.xhtml', 'nav.htm'))]
    for nav_path in nav_candidates:
        try:
            nav_html = zf.read(nav_path).decode('utf-8', errors='replace')
            # Extract text from <nav> → <ol> → <li> → <a>
            titles = re.findall(r'<a[^>]*>([^<]{1,80})</a>', nav_html)
            titles = [t.strip() for t in titles if t.strip()
                      and not t.strip().startswith(('cover', 'Cover', '封面', '版权', '目录'))]
            if len(titles) >= 3:
                logger.info("EPUB3 nav TOC: %d entries from %s", len(titles), nav_path)
                return titles[:200]
        except Exception:
            pass
    # ── EPUB2: toc.ncx ──
    ncx_candidates = [n for n in names if n.lower().endswith('.ncx')]
    for ncx_path in ncx_candidates:
        try:
            ncx_xml = zf.read(ncx_path).decode('utf-8', errors='replace')
            titles = re.findall(r'<text>([^<]{1,80})</text>', ncx_xml)
            titles = [t.strip() for t in titles if t.strip()
                      and not t.strip().startswith(('cover', 'Cover', '封面', '版权', '目录'))]
            if len(titles) >= 3:
                logger.info("EPUB2 NCX TOC: %d entries from %s", len(titles), ncx_path)
                return titles[:200]
        except Exception:
            pass
    return None




def _is_ascii_word_char(ch: str) -> bool:
    """True for Latin letters, digits, or ASCII punctuation that joins words."""
    return ch.isascii() and (ch.isalnum() or ch in "'-")




def _is_metadata_chapter(title: str, text: str) -> bool:
    """判断是否为元数据章（制作信息/简介/彩页/角色介绍/前言/目录等非正文）。

    优先按标题关键词判断；标题为空或兜底标题（第N章）时按内容特征降级判断。
    注意：『后记』对部分书籍是正文的一部分（作者感言），默认保留；
    『特典/Intermission/小剧场』是剧情补充，保留。
    """
    t = (title or "").strip()
    # 标题命中元数据关键词 → 过滤
    for kw in ("制作信息", "简介", "彩页", "插画", "角色介绍", "角色设定",
               "前言", "目录", "contents", "Contents", "封面", "版权", "书名页", "序言"):
        if kw in t:
            return True
    # 兜底标题（第N章）且内容极短 → 疑似目录页残留
    if t.startswith("第") and len(t) <= 8 and len(text or "") < 500:
        return True
    return False


def _extract_chapter_title(html: str, path: str) -> str:
    """Extract chapter title from HTML content.

    Priority: <title> → <h1> → 正文首段（无显式标记时的标题行启发式）。
    部分轻小说 epub（如败犬女主太多了）章节标题是正文首行的
    "~一败目~xxx" 或 "后记" 等，无 <title>/<h1> 标记。
    """
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = _strip_tags(m.group(1)).strip()
        if title and title not in ("Untitled", "无标题", path):
            return title[:100]
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_tags(m.group(1)).strip()[:100]

    # 正文首段标题启发式：取第一段非空文本，若符合标题特征则采用
    body = _strip_tags(html).strip()
    body = re.sub(r"\r\n", "\n", body)
    first_lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    for ln in first_lines[:3]:
        ln_clean = ln[:80]
        # 标题特征：长度 2-40、以章节符号开头（~〜·・第話）或是短句（无句号/逗号结尾）
        if 2 <= len(ln_clean) <= 40 and not ln_clean.endswith(("。", "？", "！", "，", "、", "：")):
            # 排除明显是正文句子的（含对话引号或过长）
            if "「" not in ln_clean and "『" not in ln_clean and len(ln_clean) <= 40:
                return ln_clean
    return ""




def _strip_tags(html: str) -> str:
    import re
    return re.sub(r'<[^>]+>', '', html)


# ── doc_id / series helpers (moved from ingest.py, namespaced for __init__ re-export) ──


def _infer_volume_no_impl(raw: str) -> int | None:
    """Extract 1-based volume number from a filename stem, if present."""
    if not raw:
        return None
    cn_map = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    m = re.search(
        r"(?:第\s*([一二三四五六七八九十\d]+)\s*[卷]|Vol\.?\s*(\d+)|卷\s*([一二三四五六七八九十\d]+))",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        token = next((g for g in m.groups() if g), None)
        if token:
            if token.isdigit():
                return int(token)
            if token in cn_map:
                return cn_map[token]
            if token.startswith("十") and len(token) == 2 and token[1] in cn_map:
                return 10 + cn_map[token[1]]
    # Common dump names: "书名！ 01 (作者) (Z-Library)" / sanitized "书名！__01__作者..."
    m2 = re.search(r"[！!]?[ _]*0*(\d{1,2})[ _]*(?:\(|_|$)", raw)
    if m2:
        n = int(m2.group(1))
        if 1 <= n <= 40:
            return n
    return None


def _clean_series_id_impl(raw: str) -> str:
    """Series slug: strip volume / Z-Library / author junk, keep core title."""
    text = raw or ""
    text = re.sub(r"\(Z-Library\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\d+\)\s*$", "", text)
    # Drop trailing parenthetical author / meta chunks
    text = re.sub(r"(\([^)]*\)\s*)+", "", text)
    # Sanitized dump names: "书名！__01__作者名____Z-Library__1" → keep core title
    text = re.sub(r"__.*$", "", text)
    for _ in range(2):
        text = re.sub(
            r"\s*(第[一二三四五六七八九十\d]+[卷话話]|Vol\.?\s*\d+|卷[一二三四五六七八九十\d]+|utf-?8|book\s*\d+)\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
    # Trailing " 01" / "！01" volume markers (space / full-width / underscore variants)
    text = re.sub(r"[！!]?[ _]*0*\d{1,2}[ _]*$", "", text)
    text = text.strip().rstrip("-,_！! ")
    if len(text) > 40:
        text = text[:37] + "..."
    return text or "untitled"


def _make_doc_id_impl(series_id: str, volume_no: int | None = None) -> str:
    """Build volume-safe doc_id. Keeps volume in ID to avoid multi-vol collisions."""
    sid = (series_id or "untitled").strip() or "untitled"
    if volume_no and volume_no > 0:
        return f"{sid}__vol{int(volume_no):02d}"
    return sid


def _clean_doc_id_impl(raw: str) -> str:
    """Prefer ``{series}__volNN`` when volume is present (no volume stripping collisions)."""
    vol = _infer_volume_no_impl(raw)
    series = _clean_series_id_impl(raw)
    return _make_doc_id_impl(series, vol)
