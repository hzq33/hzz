# -*- coding: utf-8 -*-
"""检索功能验证：四通道检索 + 角色图谱上下文消费。

验证：
1. NovelRetrieval.search 完整链路（intent 路由 → 多通道 → rerank）
2. 图谱缺失时 _graph_context 是否安全降级（graphs/ 目录当前为空）
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env, encoding="utf-8-sig")


async def main():
    from src.application.novel.factory import create_novel_retrieval

    retrieval = create_novel_retrieval()

    queries = [
        "八奈见杏菜和温水和彦是什么关系",
        "温水和彦的性格特点",
        "小鞠知花说过什么台词",
    ]
    for q in queries:
        print(f"\n{'='*60}\n查询: {q}\n{'='*60}")
        try:
            intent, hits = await retrieval.search_raw(q)
            print(f"意图: {intent.intent if hasattr(intent, 'intent') else intent}\n结果数: {len(hits)}")
            for r in hits[:3]:
                b = r.block
                text = (b.narrative_text or b.scene or b.question or "")
                print(f"  [{b.block_type}] {b.chapter_title} | {text[:60]!r} score={r.score:.3f}")
            gc = retrieval._graph_context(hits, doc_id=None, intent=intent)
            print(f"图谱上下文: {len(gc)} 字符 -> {gc[:100]!r}" if gc else "图谱上下文: 空（graphs/ 无落盘 → 安全降级）")
            # 完整格式化输出
            text = retrieval._format_context(q, hits, intent, doc_id=None)
            print(f"格式化上下文: {len(text)} 字符")
        except Exception as e:
            import traceback
            print(f"检索异常: {type(e).__name__}: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
