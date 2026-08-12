"""抓取 EPUB 清洗后 MD 里 * 和 ● 的真实上下文。"""

import sys
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def main():
    md = (ROOT / "data" / "史莱姆_epub_cleaned.md").read_text(encoding="utf-8")
    lines = md.split("\n")

    print(f"文件总行数: {len(lines):,}\n")

    # 找所有 * 和 ● 独占一行的位置
    targets = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s in ("*", "●", "•"):
            targets.append(i)
        elif re.match(r"^[\s*＊●•・]{2,}$", s) and ("*" in s or "●" in s or "•" in s):
            targets.append(i)

    print(f"场景分隔符总数: {len(targets)}\n")

    # 打印前 5 个的完整上下文（前后各 5 行）
    print("── 前 5 个分隔符的上下文 ──")
    for idx, line_no in enumerate(targets[:5]):
        print(f"\n[分隔符 #{idx+1}] 位置: L{line_no}")
        start = max(0, line_no - 5)
        end = min(len(lines), line_no + 6)
        for j in range(start, end):
            marker = ">>>" if j == line_no else "   "
            print(f"  {marker} L{j:>4}: {lines[j][:80]}")

    # 第 6-10 个
    if len(targets) > 5:
        print(f"\n\n── 第 6-10 个分隔符的上下文 ──")
        for idx, line_no in enumerate(targets[5:10], 6):
            print(f"\n[分隔符 #{idx}] 位置: L{line_no}")
            start = max(0, line_no - 3)
            end = min(len(lines), line_no + 4)
            for j in range(start, end):
                marker = ">>>" if j == line_no else "   "
                print(f"  {marker} L{j:>4}: {lines[j][:80]}")


if __name__ == "__main__":
    main()
