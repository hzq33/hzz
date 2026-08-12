"""验证 EPUB/TXT 双路径生成的 Chapter.text 结构一致性。

需要 DEEPSEEK_API_KEY 环境变量。
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


async def verify_file(file_path: Path, label: str):
    """跑 ingest 但只到 _parse_structure，dump 章节结构。"""
    from src.application.novel.ingest import (
        _convert_to_md, _preprocess_raw_md, _parse_structure,
    )
    import mimetypes

    print(f"\n{'='*60}")
    print(f"验证: {label}")
    print(f"文件: {file_path.name}")
    print(f"{'='*60}")

    if not file_path.exists():
        print(f"  ❌ 文件不存在")
        return

    file_bytes = file_path.read_bytes()
    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    print(f"  MIME: {mime_type}")
    print(f"  大小: {len(file_bytes):,} bytes")

    # Phase 1: 转换
    raw_md = convert_to_md(file_bytes, file_path.name, mime_type)[0]
    print(f"  转换后: {len(raw_md):,} 字符")

    # Phase 1b: 清洗
    raw_md = preprocess_raw_md(raw_md)
    print(f"  清洗后: {len(raw_md):,} 字符")

    # Phase 2: 结构解析（含 LLM 分析）
    doc_id = f"verify_{label}"
    document = await _parse_structure(raw_md, doc_id, mime_type)

    if document is None:
        print(f"  ❌ 结构解析失败")
        return

    print(f"\n  章节数: {len(document.chapters)}")
    print(f"  书名: {document.title}")

    # 验证每个章节的结构
    print(f"\n  ── 章节结构验证 ──")
    for i, ch in enumerate(document.chapters[:3]):  # 只看前 3 章
        print(f"\n  [第 {i+1} 章] {ch.title}")
        print(f"  字符数: {len(ch.text)}")

        # 检查首行是否是 # title
        first_line = ch.text.split("\n")[0]
        has_hash = first_line.startswith("# ")
        print(f"  首行: {first_line[:60]}{'...' if len(first_line) > 60 else ''}")
        print(f"  R1 (# title): {'✓' if has_hash else '✗'}")

        # 检查 R2: 空行分段
        lines = ch.text.split("\n")
        blank_lines = sum(1 for l in lines if not l.strip())
        total_lines = len(lines)
        blank_ratio = blank_lines / max(total_lines, 1)
        print(f"  R2 空行分段: 总行 {total_lines}, 空行 {blank_lines} ({blank_ratio:.1%})")

        # 检查 R4: 场景分隔符 ---
        sep_count = sum(1 for l in lines if l.strip() == "---")
        print(f"  R4 场景分隔 (---): {sep_count} 个")

        # 显示前 15 行
        print(f"\n  前 15 行预览:")
        for j, line in enumerate(lines[:15]):
            display = line[:70] + ("..." if len(line) > 70 else "")
            print(f"    L{j+1:3d}: {display}")

    # 统计全文的 R1/R2/R4 一致性
    print(f"\n  ── 全文统计 ──")
    all_have_hash = all(ch.text.startswith("# ") for ch in document.chapters)
    total_seps = sum(
        sum(1 for l in ch.text.split("\n") if l.strip() == "---")
        for ch in document.chapters
    )
    print(f"  R1 全章节有 # title: {'✓' if all_have_hash else '✗'}")
    print(f"  R4 全文 --- 总数: {total_seps}")

    # 保存第一章到文件供人工查看
    if document.chapters:
        sample = document.chapters[0]
        out_path = ROOT / "data" / f"verify_{label}_ch1.md"
        out_path.write_text(sample.text, encoding="utf-8")
        print(f"\n  第一章已保存: {out_path}")


async def main():
    epub_path = ROOT / "data" / "关于我转生变成史莱姆这档事 - 01.epub"
    txt_path = ROOT / "data" / "re0 38.txt"

    await verify_file(epub_path, "epub")
    await verify_file(txt_path, "txt")


if __name__ == "__main__":
    asyncio.run(main())
