"""混合架构验证脚本：3 个场景对比。

场景 1: 败北女角太多了！.txt     — 日轻格式, 正则应该失败 → 触发 LLM 兑底
场景 2: re0 38.txt              — 5章正则"成功"但漏序章/后记 → 完整性检查拦截 → LLM 兑底
场景 3: tests/test_novel_data/镜湖风云录.md  — 标准第N章格式 → 正则成功 + 完整性通过 → 不触发 LLM
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("verify")


async def run_one(filepath: Path, expected: dict):
    """跑一个场景的 Phase 1b + Phase 2 + 2b + 2c（不跑 Phase 3/4）."""
    print()
    print("=" * 80)
    print(f"场景: {filepath.name}")
    print(f"预期: {expected['desc']}")
    print("=" * 80)

    from src.application.novel.ingest import _detect_chapters_via_llm
    from src.domain.novel.preprocessor import (
        clean_lines, filter_binary, normalize_text, repair_paragraphs,
    )
    from src.domain.novel.parser import NovelParser

    # Phase 1
    text = filepath.read_bytes().decode("utf-8", errors="replace")
    print(f"[Phase 1] 原始字符数: {len(text)}")

    # Phase 1b (不含 repair_paragraphs)
    text, _ = filter_binary(text)
    text, _ = clean_lines(text)
    text, _ = normalize_text(text)
    print(f"[Phase 1b] 预处理后字符数: {len(text)} (未做 repair_paragraphs)")

    # Phase 2: 正则
    t0 = time.time()
    parser = NovelParser()
    document = parser.parse(text, doc_id=filepath.stem, source_format="text/plain")
    regex_chapters = len(document.chapters)
    print(f"[Phase 2] NovelParser 正则切出: {regex_chapters} 章 ({time.time()-t0:.2f}s)")

    # Phase 2b: 完整性检查
    is_complete, reason = parser.check_completeness(text, document.chapters)
    llm_triggered = False
    llm_chapters_count = 0
    if not is_complete:
        print(f"[Phase 2b] 完整性检查: 不通过 — {reason}")
        print(f"[Phase 2b] 触发 LLM 兑底...")
        t1 = time.time()
        llm_chapters = await _detect_chapters_via_llm(text, filepath.stem)
        llm_elapsed = time.time() - t1
        llm_triggered = True
        if llm_chapters:
            print(f"[Phase 2b] LLM 返回 {len(llm_chapters)} 章 (vs 正则 {regex_chapters}) ({llm_elapsed:.2f}s)")
            # 显示前 5 章标题用于诊断
            for i, ch in enumerate(llm_chapters[:5]):
                print(f"    LLM ch_{i}: {ch.title[:50]!r}")
            if len(llm_chapters) > 5:
                print(f"    ... 共 {len(llm_chapters)} 章")
        if llm_chapters and len(llm_chapters) > regex_chapters and len(llm_chapters) <= 50:
            llm_chapters_count = len(llm_chapters)
            print(f"[Phase 2b] 采用 LLM 结果 ({llm_chapters_count} 章)")
            document.chapters = llm_chapters
            document.metadata["total_chapters"] = len(llm_chapters)
            document.metadata["total_words"] = sum(len(ch.text) for ch in llm_chapters)
        else:
            llm_chapters_count = len(llm_chapters) if llm_chapters else 0
            if llm_chapters_count > 50:
                print(f"[Phase 2b] LLM 切出 {llm_chapters_count} 章过多 (>50), 可能正则误匹配, 不采用")
            else:
                print(f"[Phase 2b] LLM 未提供更好结果 (LLM={llm_chapters_count}, 正则={regex_chapters})")
    else:
        print(f"[Phase 2b] 完整性检查: 通过 — 保留正则结果")

    # Phase 2c: 分章内 repair
    for ch in document.chapters:
        try:
            ch.text, _ = repair_paragraphs(ch.text)
        except Exception as e:
            logger.warning("repair_paragraphs failed for %s: %s", ch.chapter_id, e)

    # 结果对比
    final_count = len(document.chapters)
    print()
    print(f"[结果] 最终章节数: {final_count}")
    print(f"[结果] 预期章节数: {expected['chapters']}")
    print(f"[结果] LLM 是否触发: {llm_triggered} (预期: {expected['llm_triggered']})")
    print()
    print("[章节清单]")
    for i, ch in enumerate(document.chapters):
        title_preview = ch.title[:50]
        print(f"  ch_{i:2d}: {title_preview:50s}  ({len(ch.text):6d} chars)")

    # 断言
    ok = True
    # chapters=None 表示不校验章节数（只校验 LLM 触发状态）
    if expected.get("chapters") is not None and final_count != expected["chapters"]:
        print(f"[FAIL] 章节数不符: {final_count} != {expected['chapters']}")
        ok = False
    if llm_triggered != expected["llm_triggered"]:
        print(f"[FAIL] LLM 触发状态不符: {llm_triggered} != {expected['llm_triggered']}")
        ok = False
    if ok:
        print(f"[PASS] 场景验证通过")
    else:
        print(f"[FAIL] 场景验证失败")
    return ok


async def main():
    print("混合架构验证：3 个场景")
    print("  - 场景 1: 败北女角太多了（LLM 兑底）")
    print("  - 场景 2: re0 38（正则部分成功，完整性检查拦截）")
    print("  - 场景 3: 镜湖风云录（正则成功，不触发 LLM）")

    scenarios = [
        (
            ROOT / "data" / "败北女角太多了！(败犬女主太多了！) 第一卷 utf-8.txt",
            {"desc": "日轻 ~第N败~ 格式, 正则失败 → LLM 兑底", "chapters": None, "llm_triggered": True},
        ),
        (
            ROOT / "data" / "re0 38.txt",
            {"desc": "正则切 5 章但漏序章/序幕/后记 → 完整性拦截 → LLM 兑底", "chapters": 8, "llm_triggered": True},
        ),
        (
            ROOT / "tests" / "test_novel_data" / "镜湖风云录.md",
            {"desc": "标准第N章格式, 正则成功 + 完整性通过 → 不触发 LLM", "chapters": None, "llm_triggered": False},
        ),
    ]

    results = []
    for filepath, expected in scenarios:
        if not filepath.exists():
            print(f"[SKIP] 文件不存在: {filepath}")
            results.append(False)
            continue
        try:
            ok = await run_one(filepath, expected)
            results.append(ok)
        except Exception as e:
            logger.exception("场景失败: %s", filepath)
            results.append(False)

    print()
    print("=" * 80)
    print("汇总")
    print("=" * 80)
    for (filepath, _), ok in zip(scenarios, results):
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {filepath.name}")
    print()
    if all(results):
        print("全部通过")
    else:
        print("有失败场景")


if __name__ == "__main__":
    asyncio.run(main())
