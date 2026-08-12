"""Verify LocalLLMDialogueExtractor via subprocess on 败犬女主 test text.

Uses venv_qwen subprocess for Qwen-1.8B 4-bit inference.
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TEST_TEXT = """放学后的教室里，夕阳把窗台染成橘红色。八奈见咬紧嘴唇，垂下脸。
「别说了，没关系。」
温水看着她的侧脸，叹了口气。
「我没有在说那个。你误会了。」
姬宫踩着轻快的步伐走了过来，笑容灿烂得让人睁不开眼。
「你们两个在做什么呢？放学了还不回家。」
八奈见抬起头，眼神复杂地看着姬宫。
「没什么，只是在聊天而已。姬宫你呢？」
姬宫歪了歪头，眨了眨眼睛。
「我来拿忘带的课本呀。不过看到你们两个，感觉更有趣呢。」
温水皱起眉头，转身看向窗外。
「我们该走了。再不走校门要关了。」
八奈见站起身，整理了一下裙摆。
「嗯，走吧。」
三人走出校舍，樱花花瓣在风中飞舞。姬宫突然拉住温水的袖子。
「温水同学，明天周末，要不要一起去看电影？」
八奈见脚步一顿，回过头来。
「我也一起去。」"""

def banner(t):
    print(f"\n{'='*60}\n  {t}\n{'='*60}")

def ok(msg):
    print(f"  [OK]   {msg}")

def fail(msg):
    print(f"  [FAIL] {msg}")


async def main():
    from src.domain.novel.dialogue_local_llm import LocalLLMDialogueExtractor

    banner("Initializing LocalLLMDialogueExtractor (subprocess mode)")
    t0 = time.perf_counter()
    extractor = LocalLLMDialogueExtractor()
    extractor.load()
    elapsed = time.perf_counter() - t0
    ok(f"Ready in {elapsed:.1f}s (subprocess will load model on first call)")

    banner("Extracting dialogue speakers from test text")
    t0 = time.perf_counter()
    turns = await extractor.extract_batch(TEST_TEXT)
    elapsed = time.perf_counter() - t0

    print(f"\n  Extracted {len(turns)} turns in {elapsed:.1f}s:\n")
    for t in turns:
        content = t.content[:40] + "..." if len(t.content) > 40 else t.content
        print(f"  Turn {t.turn:2d} | speaker={t.speaker:5s} | {content}")

    banner("Done")
    extractor.unload()

    if len(turns) > 0:
        ok(f"PASS — extracted {len(turns)} dialogue turns")
    else:
        fail("No turns extracted")

asyncio.run(main())
