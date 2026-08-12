"""定位每个 <hr> 在章节中的位置（章节首/中/尾）。"""

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


def main():
    epub = ROOT / "data" / "关于我转生变成史莱姆这档事 - 01.epub"

    with zipfile.ZipFile(epub) as zf:
        html_files = sorted([n for n in zf.namelist()
                             if n.endswith('.xhtml') and 'chapter' in n.lower()])

        print(f"章节文件: {len(html_files)}\n")

        for hf in html_files:
            content = zf.read(hf).decode("utf-8", errors="replace")

            # 找所有 <hr> 位置
            hr_positions = [m.start() for m in re.finditer(r"<hr[^>]*/?>", content, re.IGNORECASE)]
            if not hr_positions:
                continue

            # 找所有 <p> 内容
            p_contents = re.findall(r"<p[^>]*>(.*?)</p>", content, re.DOTALL | re.IGNORECASE)
            # 清理 HTML 标签，只保留文本
            p_texts = [re.sub(r"<[^>]+>", "", pc).strip() for pc in p_contents]
            p_texts = [t for t in p_texts if t]

            total_p = len(p_texts)
            print(f"── {hf} ──")
            print(f"  <p> 段落数: {total_p}")
            print(f"  <hr> 数量: {len(hr_positions)}")

            # 通过文本长度估算 hr 在章节的相对位置
            total_len = sum(len(t) for t in p_texts)
            print(f"  总字符: {total_len}")

            # 找 hr 之前最近的 <p> 内容（用 hr 在原文中的位置）
            for hr_pos in hr_positions:
                # 找 hr 之前所有 <p> 标签的起始位置
                p_starts = [(m.start(), m.group(0)) for m in re.finditer(r"<p[^>]*>", content, re.IGNORECASE)]
                # 找 hr 之前最后一个 <p> 的内容
                prev_p_text = ""
                for i, (p_start, _) in enumerate(p_starts):
                    if p_start > hr_pos:
                        if i > 0:
                            # 取前一个 p 的内容
                            prev_p_start = p_starts[i-1][0]
                            seg = content[prev_p_start:hr_pos]
                            texts = re.findall(r"<p[^>]*>(.*?)</p>", seg, re.DOTALL | re.IGNORECASE)
                            if texts:
                                prev_p_text = re.sub(r"<[^>]+>", "", texts[-1]).strip()[:60]
                        break

                # 计算 hr 之前的总文本长度
                pre_text = content[:hr_pos]
                pre_p_texts = re.findall(r"<p[^>]*>(.*?)</p>", pre_text, re.DOTALL | re.IGNORECASE)
                pre_len = sum(len(re.sub(r"<[^>]+>", "", t).strip()) for t in pre_p_texts)
                ratio = pre_len / max(total_len, 1)

                position = "章节首" if ratio < 0.1 else \
                           "章节中" if ratio < 0.9 else "章节尾"

                print(f"  <hr> 位置: {position} (前文 {pre_len}/{total_len} = {ratio:.0%})")
                if prev_p_text:
                    print(f"    hr 前一段: {prev_p_text}")

            print()


if __name__ == "__main__":
    main()
