"""验证清洗后 MD 文件的标题形态 — 评估章节检测覆盖率。

对 data/ 下三个测试文件（txt/epub/md）跑完整清洗流程，
输出每个文件清洗后所有可能的标题行（# 开头、第N章、序章等）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 修复 Windows 控制台编码
if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # 让 src.* 可导入

# ── 1. 清洗流程（复刻 ingest._preprocess_raw_md） ──────────────

def preprocess_raw_md(raw_md: str) -> str:
    from src.domain.novel.preprocessor import (
        filter_binary, clean_lines, normalize_text,
    )
    for stage_fn, name in [
        (filter_binary, "binary_filter"),
        (clean_lines, "line_clean"),
        (normalize_text, "normalize"),
    ]:
        try:
            raw_md, _ = stage_fn(raw_md)
        except Exception as e:
            print(f"  [warn] stage {name} failed: {e}")
    return raw_md


def convert_epub_to_md(file_bytes: bytes) -> str:
    from src.application.novel.ingest import convert_epub
    return convert_epub(file_bytes, "test.epub")[0]


def detect_and_transcode(file_bytes: bytes) -> str:
    from src.domain.novel.preprocessor import detect_and_transcode
    text, _ = detect_and_transcode(file_bytes)
    return text


# ── 2. 标题识别正则（覆盖所有可能的章节标题形态） ──────────────

# 现有 NovelParser 的 5 条正则
PARSER_PATTERNS = [
    ("markdown_heading", re.compile(r"^#{1,3}\s+(.+)", re.MULTILINE)),
    ("zh_chapter", re.compile(r"^第[一二三四五六七八九十百千零两\d]+[章回节卷篇部]", re.MULTILINE)),
    ("en_chapter", re.compile(r"^(?:CHAPTER|Chapter|chapter)\s+(?:[IVXLCDM]+|\d+|[A-Z][a-z]+)", re.MULTILINE)),
    ("paren_number", re.compile(r"^[（(【]\s*[一二三四五六七八九十百千零两\d]+\s*[）)】]", re.MULTILINE)),
    ("bare_number", re.compile(r"^[一二三四五六七八九十百千零两\d]+[、.．]\s*$", re.MULTILINE)),
]

# 扩充候选正则（项目记忆里 LLM 生成的、NovelParser 缺失的）
EXTENDED_PATTERNS = [
    # 序章/序幕/后记/楔子等
    ("prologue_afterword", re.compile(
        r"^(序章|序幕|序言|楔子|引子|引言|后记|后序|跋|尾声|终章|终焉|番外篇?|间章|间奏|插曲|序)\s*$",
        re.MULTILINE,
    )),
    # 第N话/幕/败/战
    ("ja_lightnovel", re.compile(
        r"^第[一二三四五六七八九十百千零两\d]+[话話幕败戰戰节節]",
        re.MULTILINE,
    )),
    # Episode N
    ("episode", re.compile(r"^Episode\s*\d+", re.MULTILINE)),
    # ～第N败～ 带装饰符
    ("decorated", re.compile(r"^[～~\-—=·*]+.*第[一二三四五六七八九十百千零两\d]+.*[～~\-—=·*]+$", re.MULTILINE)),
]

ALL_PATTERNS = PARSER_PATTERNS + EXTENDED_PATTERNS


def find_all_headings(text: str) -> list[tuple[str, str, int]]:
    """返回 (pattern_name, matched_line, line_number) 列表。"""
    results = []
    seen_lines = set()
    for name, pattern in ALL_PATTERNS:
        for m in pattern.finditer(text):
            line_idx = text[:m.start()].count("\n")
            if line_idx in seen_lines:
                continue
            seen_lines.add(line_idx)
            line = m.group(0).strip()
            results.append((name, line, line_idx))
    # 按行号排序
    results.sort(key=lambda x: x[2])
    return results


def analyze_file(filename: str, raw_md: str) -> None:
    """分析单个文件清洗后的标题形态。"""
    print(f"\n{'='*70}")
    print(f"文件: {filename}")
    print(f"清洗后字符数: {len(raw_md):,}")
    print(f"{'='*70}")

    # 清洗
    cleaned = preprocess_raw_md(raw_md)
    print(f"清洗后字符数: {len(cleaned):,} (变化 {len(cleaned)-len(raw_md):+,})")

    # 找标题
    headings = find_all_headings(cleaned)

    if not headings:
        print("\n[!] 未找到任何标题行")
        return

    # 按正则类型分组统计
    from collections import Counter
    by_pattern = Counter(h[0] for h in headings)

    print(f"\n共找到 {len(headings)} 个候选标题行")
    print(f"按类型分布: {dict(by_pattern)}")

    # 区分：现有正则能匹配的 vs 需要扩充的
    parser_matched = [h for h in headings if h[0] in dict(PARSER_PATTERNS).keys() or h[0] == "markdown_heading"]
    extended_only = [h for h in headings if h[0] in dict(EXTENDED_PATTERNS).keys()]

    print(f"\n现有 NovelParser 正则可匹配: {len(parser_matched)} 条")
    print(f"需扩充正则才能匹配: {len(extended_only)} 条")

    # 输出所有标题
    print(f"\n── 标题清单（前 30 条）──")
    for i, (pname, line, lineno) in enumerate(headings[:30]):
        marker = "✓" if pname in dict(PARSER_PATTERNS).keys() or pname == "markdown_heading" else "✗"
        print(f"  {marker} L{lineno:>5} [{pname:>18}] {line[:60]}")

    if len(headings) > 30:
        print(f"  ... 还有 {len(headings)-30} 条")

    # 完整性检查：首尾残留
    if headings:
        first_lineno = headings[0][2]
        last_lineno = headings[-1][2]
        total_lines = cleaned.count("\n") + 1
        leading_ratio = first_lineno / max(total_lines, 1)
        trailing_ratio = (total_lines - last_lineno) / max(total_lines, 1)
        print(f"\n── 完整性 ──")
        print(f"  首行: L{first_lineno}/{total_lines} (leading ratio {leading_ratio:.1%})")
        print(f"  末行: L{last_lineno}/{total_lines} (trailing ratio {trailing_ratio:.1%})")
        if leading_ratio > 0.05:
            print(f"  [!] 首部残留 {first_lineno} 行，可能漏掉序章")
        if trailing_ratio > 0.05:
            print(f"  [!] 尾部残留 {total_lines - last_lineno} 行，可能漏掉后记")


# ── 3. 主流程 ──────────────────────────────────────────────

def main():
    # 三个测试文件
    files = [
        ("re0 38.txt", "data/re0 38.txt"),
        ("败北女角太多了！(第一卷).txt", "data/败北女角太多了！(败犬女主太多了！) 第一卷 utf-8.txt"),
        ("关于我转生变成史莱姆这档事 - 01.epub", "data/关于我转生变成史莱姆这档事 - 01.epub"),
    ]

    # 也看一下已经清洗过的 md 验证文件
    md_files = [
        ("re0 38_验证.md", "data/re0 38_验证.md"),
        ("败北女角太多了！_方案A验证.md", "data/败北女角太多了！_方案A验证.md"),
    ]

    print("=" * 70)
    print("阶段 1: 验证源文件清洗后的标题形态")
    print("=" * 70)

    for label, rel_path in files:
        path = ROOT / rel_path
        if not path.exists():
            print(f"\n[skip] 文件不存在: {path}")
            continue
        try:
            file_bytes = path.read_bytes()
            if path.suffix == ".epub":
                raw_md = convert_epub_to_md(file_bytes)
            else:
                raw_md = detect_and_transcode(file_bytes)
            analyze_file(label, raw_md)
        except Exception as e:
            print(f"\n[error] 处理 {label} 失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n\n" + "=" * 70)
    print("阶段 2: 验证已有 MD 验证文件（已清洗过）的标题形态")
    print("=" * 70)

    for label, rel_path in md_files:
        path = ROOT / rel_path
        if not path.exists():
            print(f"\n[skip] 文件不存在: {path}")
            continue
        try:
            raw_md = path.read_text(encoding="utf-8", errors="replace")
            # 这些文件已经清洗过，直接分析
            print(f"\n{'='*70}")
            print(f"文件: {label} (已清洗)")
            print(f"字符数: {len(raw_md):,}")
            print(f"{'='*70}")

            headings = find_all_headings(raw_md)
            if not headings:
                print("[!] 未找到任何标题行")
                continue

            from collections import Counter
            by_pattern = Counter(h[0] for h in headings)
            print(f"\n共找到 {len(headings)} 个候选标题行")
            print(f"按类型分布: {dict(by_pattern)}")

            parser_matched = sum(1 for h in headings if h[0] in dict(PARSER_PATTERNS).keys() or h[0] == "markdown_heading")
            extended_only = sum(1 for h in headings if h[0] in dict(EXTENDED_PATTERNS).keys())
            print(f"现有正则可匹配: {parser_matched} 条")
            print(f"需扩充正则: {extended_only} 条")

            print(f"\n── 标题清单 ──")
            for pname, line, lineno in headings[:30]:
                marker = "✓" if pname in dict(PARSER_PATTERNS).keys() or pname == "markdown_heading" else "✗"
                print(f"  {marker} L{lineno:>5} [{pname:>18}] {line[:60]}")

        except Exception as e:
            print(f"\n[error] 处理 {label} 失败: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
