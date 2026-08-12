"""检查 EPUB 源文件里 * 和 ● 符号的位置和上下文。"""

import sys
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def strip_html(text: str) -> str:
    """简单去 HTML 标签，保留文本。"""
    # 先把 <p> 转成换行
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<hr[^>]*/?>", "\n---HR---\n", text, flags=re.IGNORECASE)
    # 去所有标签
    text = re.sub(r"<[^>]+>", "", text)
    # 解码常见实体
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return text


def main():
    epub = ROOT / "data" / "关于我转生变成史莱姆这档事 - 01.epub"

    with zipfile.ZipFile(epub) as zf:
        html_files = sorted([n for n in zf.namelist()
                             if n.endswith('.xhtml') and 'chapter' in n.lower()])

        # 统计 * 和 ● 在原文中的出现
        star_total = 0
        dot_total = 0
        star_examples = []  # (file, context_before, line, context_after)
        dot_examples = []

        for hf in html_files:
            raw = zf.read(hf).decode("utf-8", errors="replace")
            text = strip_html(raw)
            lines = text.split("\n")

            for i, line in enumerate(lines):
                stripped = line.strip()

                # 检测纯符号行（场景分隔符的典型形态）
                # * 号：* * * / *** / * * 等
                if re.match(r"^[\s*＊]{2,}$", stripped) and "*" in stripped or stripped == "* * *":
                    star_total += 1
                    if len(star_examples) < 8:
                        prev_line = lines[i-1].strip()[:50] if i > 0 else ""
                        next_line = lines[i+1].strip()[:50] if i+1 < len(lines) else ""
                        star_examples.append((hf, prev_line, stripped, next_line))

                # ● 号：● / ● ● ● / ●●● 等
                if re.match(r"^[\s●•・]{2,}$", stripped) and "●" in stripped:
                    dot_total += 1
                    if len(dot_examples) < 8:
                        prev_line = lines[i-1].strip()[:50] if i > 0 else ""
                        next_line = lines[i+1].strip()[:50] if i+1 < len(lines) else ""
                        dot_examples.append((hf, prev_line, stripped, next_line))

                # 单个 * 或 ●（短行）
                if stripped in ("*", "●", "•") or re.match(r"^[*●•]\s+[*●•]\s+[*●•]$", stripped):
                    if "*" in stripped:
                        if len(star_examples) < 8:
                            prev_line = lines[i-1].strip()[:50] if i > 0 else ""
                            next_line = lines[i+1].strip()[:50] if i+1 < len(lines) else ""
                            star_examples.append((hf, prev_line, stripped, next_line))
                        star_total += 1
                    else:
                        if len(dot_examples) < 8:
                            prev_line = lines[i-1].strip()[:50] if i > 0 else ""
                            next_line = lines[i+1].strip()[:50] if i+1 < len(lines) else ""
                            dot_examples.append((hf, prev_line, stripped, next_line))
                        dot_total += 1

        print(f"── EPUB 源文件中场景分隔符统计 ──")
        print(f"  * 星号类分隔符: {star_total} 处")
        print(f"  ● 圆点类分隔符: {dot_total} 处")

        if star_examples:
            print(f"\n── * 星号类示例（前 {len(star_examples)} 个）──")
            for hf, prev, line, nxt in star_examples:
                print(f"  [{hf}]")
                print(f"    前文: {prev}")
                print(f"    分隔: |{line}|")
                print(f"    后文: {nxt}")
                print()

        if dot_examples:
            print(f"\n── ● 圆点类示例（前 {len(dot_examples)} 个）──")
            for hf, prev, line, nxt in dot_examples:
                print(f"  [{hf}]")
                print(f"    前文: {prev}")
                print(f"    分隔: |{line}|")
                print(f"    后文: {nxt}")
                print()

        # 顺便统计清洗后 MD 里这些符号的情况
        print(f"\n── 清洗后 MD 里 * 和 ● 的统计 ──")
        md = (ROOT / "data" / "史莱姆_epub_cleaned.md").read_text(encoding="utf-8")
        md_lines = md.split("\n")
        star_in_md = 0
        dot_in_md = 0
        for line in md_lines:
            s = line.strip()
            if re.match(r"^[\s*＊]{2,}$", s) and "*" in s:
                star_in_md += 1
            if re.match(r"^[\s●•・]{2,}$", s) and "●" in s:
                dot_in_md += 1
            if s in ("*", "●", "•"):
                if "*" in s:
                    star_in_md += 1
                else:
                    dot_in_md += 1

        print(f"  * 星号: {star_in_md} 处")
        print(f"  ● 圆点: {dot_in_md} 处")


if __name__ == "__main__":
    main()
