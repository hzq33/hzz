"""验证 EPUB 转 MD 质量。

检查项：
  - OPF spine 阅读顺序
  - 章节标题提取
  - 段落结构（CJK 无空格拼接）
  - 章节数量与首尾字符
  - 检测是否有过短/异常章节
  - 喂入 NovelParser 后的章节切分
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("verify_epub")


def main():
    from src.application.novel.ingest import convert_epub
    from src.domain.novel.preprocessor import (
        clean_lines, filter_binary, normalize_text, repair_paragraphs,
    )
    from src.domain.novel.parser import NovelParser

    epub_path = ROOT / "data" / "关于我转生变成史莱姆这档事 - 01.epub"
    if not epub_path.exists():
        print(f"[FAIL] 测试文件不存在: {epub_path}")
        return

    file_bytes = epub_path.read_bytes()
    print(f"[Phase 1] EPUB: {epub_path.name} ({len(file_bytes)} bytes)")

    # Phase 1: 转换
    raw_md = convert_epub(file_bytes, epub_path.name)[0]
    print(f"[Phase 1] 转换后字符数: {len(raw_md)}")

    # 检查转换结果
    preview_head = raw_md[:1500]
    preview_tail = raw_md[-1000:]
    print("\n--- 文件头部 (前 1500 字符) ---")
    print(preview_head)
    print("\n--- 文件尾部 (后 1000 字符) ---")
    print(preview_tail)

    # 统计 # 标题数
    import re
    h1_count = len(re.findall(r'^# .+$', raw_md, re.MULTILINE))
    h2_count = len(re.findall(r'^## .+$', raw_md, re.MULTILINE))
    h3_count = len(re.findall(r'^### .+$', raw_md, re.MULTILINE))
    print(f"\n[统计] H1: {h1_count}, H2: {h2_count}, H3: {h3_count}")

    # Phase 1b: 预处理（不含 repair_paragraphs）
    text = raw_md
    text, _ = filter_binary(text)
    text, _ = clean_lines(text)
    text, _ = normalize_text(text)
    print(f"[Phase 1b] 预处理后字符数: {len(text)}")

    # Phase 2: 章节解析
    parser = NovelParser()
    document = parser.parse(text, doc_id=epub_path.stem, source_format="application/epub+zip")
    print(f"[Phase 2] NovelParser 切出 {len(document.chapters)} 章, title='{document.title}'")

    # 完整性检查
    is_complete, reason = parser.check_completeness(text, document.chapters)
    print(f"[Phase 2b] 完整性检查: {'通过' if is_complete else '不通过 - ' + reason}")

    # 显示前 10 章
    print("\n--- 前 10 章 ---")
    for i, ch in enumerate(document.chapters[:10]):
        title_preview = (ch.title or "")[:50]
        body_head = (ch.text or "")[:80].replace('\n', ' ')
        print(f"  ch_{i:2d}: {title_preview:50s}  ({len(ch.text):6d} chars) | {body_head}...")

    if len(document.chapters) > 10:
        print(f"  ... 共 {len(document.chapters)} 章")

    # 检查异常短章节
    short_chapters = [(i, ch) for i, ch in enumerate(document.chapters) if len(ch.text) < 100]
    if short_chapters:
        print(f"\n[警告] {len(short_chapters)} 个过短章节 (<100 字符):")
        for i, ch in short_chapters[:5]:
            print(f"  ch_{i}: {ch.title[:50]!r} ({len(ch.text)} chars) text={ch.text[:80]!r}")

    # 检查章节标题重复
    titles = [ch.title for ch in document.chapters if ch.title]
    from collections import Counter
    dupes = {t: c for t, c in Counter(titles).items() if c > 1}
    if dupes:
        print(f"\n[警告] 重复章节标题:")
        for t, c in dupes.items():
            print(f"  '{t[:50]}' x {c}")

    # 检查段落空格问题（CJK 之间不应有空格）
    # 取一章节样本检查
    if document.chapters:
        sample = document.chapters[len(document.chapters) // 2].text[:500]
        cjk_space_pattern = re.compile(r'[\u4e00-\u9fff]\s+[\u4e00-\u9fff]')
        space_issues = cjk_space_pattern.findall(sample)
        if space_issues:
            print(f"\n[警告] CJK 间存在空格 (示例): {space_issues[:5]}")
            print(f"  样本: {sample[:200]!r}")
        else:
            print(f"\n[OK] CJK 段落无异常空格")


if __name__ == "__main__":
    main()
