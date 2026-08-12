"""排查败北女角预处理后 ~第一败~ 标题的位置."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.domain.novel.preprocessor import clean_lines, filter_binary, normalize_text

text = (ROOT / "data" / "败北女角太多了！(败犬女主太多了！) 第一卷 utf-8.txt").read_bytes().decode("utf-8", errors="replace")
text, _ = filter_binary(text)
text, _ = clean_lines(text)
text, _ = normalize_text(text)

# 查找所有 ~第 标记
import re
matches = list(re.finditer(r"~第[^~\n]{1,30}~", text))
print(f"找到 {len(matches)} 个 ~第...~ 标记:")
for m in matches[:20]:
    # 显示前后各 50 字符的上下文
    start = max(0, m.start() - 50)
    end = min(len(text), m.end() + 50)
    ctx = text[start:end].replace("\n", "\\n")
    print(f"  pos={m.start():6d}  match={m.group(0)!r}")
    print(f"    ctx: ...{ctx}...")
    print()

# 查找 LLM 正则匹配
llm_regex = r"^\s*(?:序[章言幕]?|序幕|楔子|引子|前言|卷首|开篇|Prologue|第[\d一二三四五六七八九十百千万零两壹贰叁肆伍陆柒捌玖拾佰仟]+[章回节卷篇部话話败勝幕折]|[~～◆◇▲■○●△▽★☆]?第[\d一二三四五六七八九十百千万零两壹贰叁肆伍陆柒捌玖拾佰仟]+[章回节卷篇部话話败勝幕折][~～◆◇▲■○●△▽★☆]?|尾声|终章|结章|Fin|Epilogue|后记|跋|番外|外传|特典|SS|Side\s+Story|Intermission|Appendix|Afterword).*"
pattern = re.compile(llm_regex, re.MULTILINE)
llm_matches = list(pattern.finditer(text))
print(f"\nLLM 正则匹配数: {len(llm_matches)}")
print("\n前 20 个匹配:")
for m in llm_matches[:20]:
    line = m.group(0).strip()
    print(f"  pos={m.start():6d}  {line[:60]}")
print("\n后 5 个匹配:")
for m in llm_matches[-5:]:
    line = m.group(0).strip()
    print(f"  pos={m.start():6d}  {line[:60]}")
