"""检查 EPUB 源 HTML 里的场景分隔标记（hr、空 p、特殊符号等）。"""

import sys
import re
import zipfile
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def main():
    epub = ROOT / "data" / "关于我转生变成史莱姆这档事 - 01.epub"
    print(f"读取: {epub}\n")

    with zipfile.ZipFile(epub) as zf:
        html_files = [n for n in zf.namelist()
                      if n.endswith(('.html', '.xhtml', '.htm'))]

        print(f"HTML 文件数: {len(html_files)}")

        # 统计所有 HTML 里的标签
        tag_counter = Counter()
        hr_count = 0
        empty_p_count = 0
        hr_examples = []

        for hf in html_files:
            try:
                content = zf.read(hf).decode("utf-8", errors="replace")
            except Exception as e:
                continue

            # 统计所有标签
            tags = re.findall(r"<(\w+)[\s/>]", content)
            tag_counter.update(tags)

            # 找 <hr> 标签
            hrs = re.findall(r"<hr[^>]*/?>", content, re.IGNORECASE)
            if hrs:
                hr_count += len(hrs)
                # 抓上下文
                for m in re.finditer(r".{0,80}<hr[^>]*/?>.{0,80}", content, re.IGNORECASE):
                    if len(hr_examples) < 5:
                        ctx = m.group(0).replace("\n", " ").strip()
                        hr_examples.append((hf, ctx))

            # 找空 <p></p> 或 <p> </p>
            empty_ps = re.findall(r"<p[^>]*>\s*</p>", content, re.IGNORECASE)
            empty_p_count += len(empty_ps)

        print(f"\n── HTML 标签统计（前 20） ──")
        for tag, count in tag_counter.most_common(20):
            print(f"  <{tag:>10}>: {count}")

        print(f"\n── 场景分隔相关 ──")
        print(f"  <hr> 标签总数: {hr_count}")
        print(f"  空 <p></p> 总数: {empty_p_count}")

        if hr_examples:
            print(f"\n── <hr> 上下文示例 ──")
            for hf, ctx in hr_examples:
                print(f"  [{hf}]")
                print(f"    {ctx[:160]}")

        # 看一个内容文件的实际结构
        print(f"\n── 抽样：第一个内容文件的前 80 行 ──")
        for hf in html_files:
            if "nav" in hf.lower() or "toc" in hf.lower():
                continue
            content = zf.read(hf).decode("utf-8", errors="replace")
            lines = content.split("\n")[:80]
            for i, line in enumerate(lines):
                print(f"  L{i:>3}: {line[:100]}")
            break


if __name__ == "__main__":
    main()
