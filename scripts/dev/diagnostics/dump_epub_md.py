"""把 EPUB 转换后的 MD 落盘，供人工审阅结构。"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def main():
    from src.application.novel.ingest import convert_to_md, preprocess_raw_md

    src = ROOT / "data" / "关于我转生变成史莱姆这档事 - 01.epub"
    dst_raw = ROOT / "data" / "史莱姆_epub_raw.md"          # _convert_to_md 直接输出
    dst_cleaned = ROOT / "data" / "史莱姆_epub_cleaned.md"  # 经过 _preprocess_raw_md

    print(f"读取: {src}")
    file_bytes = src.read_bytes()

    # 1. _convert_to_md 直接输出（未经 preprocessor）
    raw_md = convert_to_md(file_bytes, src.name, "application/epub+zip")[0]
    print(f"\n[1] _convert_to_md 输出字符数: {len(raw_md):,}")
    dst_raw.write_text(raw_md, encoding="utf-8")
    print(f"    已保存: {dst_raw}  ({dst_raw.stat().st_size:,} bytes)")

    # 2. 经过 _preprocess_raw_md（现有 ingest 流程实际使用的形态）
    cleaned = preprocess_raw_md(raw_md)
    print(f"\n[2] _preprocess_raw_md 清洗后字符数: {len(cleaned):,}  (变化 {len(cleaned)-len(raw_md):+,})")
    dst_cleaned.write_text(cleaned, encoding="utf-8")
    print(f"    已保存: {dst_cleaned}  ({dst_cleaned.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
