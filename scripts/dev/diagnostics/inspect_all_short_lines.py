"""检查 EPUB 清洗后 MD 里所有孤立短行（潜在场景分隔符）。"""

import sys
import re
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def main():
    md = (ROOT / "data" / "史莱姆_epub_cleaned.md").read_text(encoding="utf-8")
    lines = md.split("\n")

    # 找所有短行（≤10 字符的非空行）
    short_lines = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s and len(s) <= 10:
            short_lines.append((i, s))

    print(f"文件总行数: {len(lines):,}")
    print(f"短行（≤10 字符非空）总数: {len(short_lines)}\n")

    # 按内容统计
    counter = Counter(s for _, s in short_lines)
    print("── 短行内容分布（前 30） ──")
    for content, count in counter.most_common(30):
        # 判断是否是场景分隔符特征
        is_sep = bool(re.match(r"^[\d\*＊●•・·─━─=\-~～\s]+$", content))
        marker = " [SEP]" if is_sep else ""
        print(f"  出现 {count:>3} 次: |{content}|{marker}")

    # 看所有被识别为场景分隔符的行，验证上下文
    print("\n── 所有 SEP 类短行的上下文验证 ──")
    sep_pattern = re.compile(r"^[\d\*＊●•・·─━─=\-~～\s]+$")
    seps_with_context = []
    for i, s in short_lines:
        if sep_pattern.match(s):
            prev_line = lines[i-1].strip()[:40] if i > 0 else ""
            next_line = lines[i+1].strip()[:40] if i+1 < len(lines) else ""
            seps_with_context.append((i, s, prev_line, next_line))

    print(f"SEP 类短行总数: {len(seps_with_context)}")
    print(f"\n前 10 个上下文：")
    for idx, (lineno, s, prev, nxt) in enumerate(seps_with_context[:10]):
        print(f"\n  [#{idx+1}] L{lineno}: |{s}|")
        print(f"    前文: {prev}")
        print(f"    后文: {nxt}")

    # 也看一下 re0 38 TXT 的场景分隔符
    print("\n\n" + "=" * 60)
    print("对比：re0 38 TXT 清洗后")
    print("=" * 60)

    txt_md = (ROOT / "data" / "re0 38_cleaned.md").read_text(encoding="utf-8")
    txt_lines = txt_md.split("\n")
    txt_short = []
    for i, line in enumerate(txt_lines):
        s = line.strip()
        if s and len(s) <= 10:
            txt_short.append((i, s))

    txt_counter = Counter(s for _, s in txt_short)
    print(f"短行总数: {len(txt_short)}")
    print("\n── 短行内容分布（前 30） ──")
    for content, count in txt_counter.most_common(30):
        is_sep = bool(re.match(r"^[\d\*＊●•・·─━─=\-~～\s]+$", content))
        marker = " [SEP]" if is_sep else ""
        print(f"  出现 {count:>3} 次: |{content}|{marker}")


if __name__ == "__main__":
    main()
