"""对比 EPUB vs TXT 清洗后的排版差异，为统一化设计提供依据。"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def show_sample(label: str, text: str, n_head: int = 50, n_tail: int = 10) -> None:
    print(f"\n{'='*70}")
    print(f"{label}")
    print(f"总字符: {len(text):,}  总行数: {text.count(chr(10))+1:,}")
    print(f"{'='*70}")

    lines = text.split("\n")
    print(f"\n── 前 {n_head} 行 ──")
    for i, line in enumerate(lines[:n_head]):
        # 显示行首空格和行尾空白
        repr_line = line.replace(" ", "·").replace("\t", "→")
        print(f"L{i:>3}: {repr_line[:80]}")

    print(f"\n── 后 {n_tail} 行 ──")
    start = max(0, len(lines) - n_tail)
    for i, line in enumerate(lines[start:], start=start):
        repr_line = line.replace(" ", "·").replace("\t", "→")
        print(f"L{i:>3}: {repr_line[:80]}")


def stats(label: str, text: str) -> None:
    """统计排版特征。"""
    lines = text.split("\n")
    non_empty = [l for l in lines if l.strip()]

    # 段落分隔统计
    blank_1 = text.count("\n\n")  # 单空行
    blank_2 = len(__import__("re").findall(r"\n{3,}", text))  # 多空行

    # 缩进统计
    leading_space = sum(1 for l in non_empty if l != l.lstrip())
    leading_space_4plus = sum(1 for l in non_empty if len(l) - len(l.lstrip(" ·　")) >= 4)

    # 孤立短行（可能是场景分隔符）
    short_lines = sum(1 for l in non_empty if len(l.strip()) <= 5)

    # 引号统计
    import re
    ja_quotes = len(re.findall(r"[「」『』]", text))
    cn_quotes = len(re.findall(r"[“”]", text))

    # markdown 标记
    md_headings = len(re.findall(r"^#{1,3}\s+", text, re.MULTILINE))

    print(f"\n── {label} 排版特征 ──")
    print(f"  非空行数: {len(non_empty):,}")
    print(f"  单空行(\\n\\n): {blank_1:,}")
    print(f"  多空行(\\n{{3,}}): {blank_2:,}")
    print(f"  行首有空格: {leading_space:,} ({leading_space/max(len(non_empty),1):.1%})")
    print(f"  行首≥4空格: {leading_space_4plus:,}")
    print(f"  孤立短行(≤5字): {short_lines:,}")
    print(f"  日式引号「」『』: {ja_quotes:,}")
    print(f"  中式引号“”: {cn_quotes:,}")
    print(f"  markdown标题: {md_headings}")


def main():
    from src.domain.novel.preprocessor import detect_and_transcode
    from src.application.novel.ingest import convert_to_md, preprocess_raw_md

    # 1. EPUB 路径
    epub_path = ROOT / "data" / "关于我转生变成史莱姆这档事 - 01.epub"
    if epub_path.exists():
        epub_bytes = epub_path.read_bytes()
        epub_md = convert_to_md(epub_bytes, epub_path.name, "application/epub+zip")[0]
        epub_cleaned = preprocess_raw_md(epub_md)

        show_sample("EPUB 清洗后（前50行）", epub_cleaned)
        stats("EPUB", epub_cleaned)

    # 2. TXT 路径
    txt_path = ROOT / "data" / "re0 38.txt"
    if txt_path.exists():
        txt_bytes = txt_path.read_bytes()
        txt_md, _ = detect_and_transcode(txt_bytes)
        txt_cleaned = preprocess_raw_md(txt_md)

        show_sample("TXT 清洗后（前50行）", txt_cleaned)
        stats("TXT", txt_cleaned)


if __name__ == "__main__":
    main()
