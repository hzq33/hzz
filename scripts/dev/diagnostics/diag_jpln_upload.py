"""中译日轻小说全链路测试 — 验证 DialogueExtractor 上下文推断与角色发现。

中译日轻特点（区别于中文原创）：
  1. 对话用「」（全角方括号），非中文 ""
  2. 说话人与对话常分离在不同行（上下文推断核心场景）
  3. 角色名为中文化的日式名（八奈见、温水、姬宫）
  4. JP 模式不触发（无假名），靠 CN Pattern 4 + _infer_speaker_from_context

测试文本参考《败败女角太多了！》风格构造。
"""

import asyncio
import os
import sys
import shutil
import traceback
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DEEPSEEK_API_KEY", None)


def banner(t):
    print(f"\n{'='*70}\n {t}\n{'='*70}")

def step(t):
    print(f"\n--- {t} ---")

def ok(msg):
    print(f"  [OK]   {msg}")

def fail(msg, err=""):
    print(f"  [FAIL] {msg}" + (f"\n         {err}" if err else ""))

def warn(msg):
    print(f"  [WARN] {msg}")


# 中译日轻小说测试文本（参考《败败女角太多了！》风格）
# 关键场景：说话人与「」分离，需上下文推断
JAPANESE_LN_CN = """# 败北女角太多了

## 第一败 青春之恋总是败北

放学后的教室里，夕阳把窗台染成橘红色。八奈见咬紧嘴唇，垂下脸。

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

「我也一起去。」

姬宫捂嘴轻笑。

「哎呀，八奈见同学也来？那更热闹了呢。」

温水挠了挠头，一脸无奈。

「随便你们吧。反正我只是付钱的那个人。」

## 第二败 即使如此她还是不放弃

第二天，电影院门口。八奈见穿着淡蓝色的连衣裙，比平时更用心地打扮过。

温水看到她，愣了一下。

「你……今天穿得不一样。」

八奈见别过脸去，耳根微红。

「只是……碰巧而已。别多想。」

姬宫从后面抱住八奈见的胳膊。

「八奈见同学今天好可爱呀！简直像约会一样。」

八奈见想抽出手，却被抱得更紧。

「姬宫！别在这种地方……」

温水叹了口气，走向售票处。

「两份学生票。」

八奈见挣脱姬宫，追了上去。

「我自己付自己的。」

温水头也不回。

「别客气。反正我欠你上次帮忙的。」

八奈见沉默片刻，低下声音。

「……谢谢。」
"""


async def main():
    banner("中译日轻小说全链路测试")
    print(f"测试文本: {len(JAPANESE_LN_CN)} chars, 2 章")

    # ── 1. DialogueExtractor 单元测试 ──
    banner("Stage 1: DialogueExtractor 上下文推断测试")
    from src.domain.novel.dialogue import DialogueExtractor

    extractor = DialogueExtractor()
    # 取第一章前几段，重点看说话人识别
    sample = """八奈见咬紧嘴唇，垂下脸。

「别说了，没关系。」

温水看着她的侧脸，叹了口气。

「我没有在说那个。你误会了。」

姬宫踩着轻快的步伐走了过来，笑容灿烂得让人睁不开眼。

「你们两个在做什么呢？放学了还不回家。」

八奈见抬起头，眼神复杂地看着姬宫。

「没什么，只是在聊天而已。姬宫你呢？」"""

    step("1.1 提取对话 + 说话人识别")
    turns = extractor.extract(sample, scene="放学后的教室", doc_id="test", chapter_title="第一败")
    print(f"  提取到 {len(turns)} 轮对话:")
    correct = 0
    expected = [
        ("八奈见", "别说了，没关系。"),
        ("温水", "我没有在说那个。你误会了。"),
        ("姬宫", "你们两个在做什么呢？放学了还不回家。"),
        ("八奈见", "没什么，只是在聊天而已。姬宫你呢？"),
    ]
    for i, t in enumerate(turns):
        exp_spk, exp_cnt = expected[i] if i < len(expected) else ("?", "?")
        match = "✓" if t.speaker == exp_spk else "✗"
        print(f"    Turn {t.turn}: {match} speaker='{t.speaker}' (期望='{exp_spk}') "
              f"mood='{t.mood}' content='{t.content[:20]}...'")
        if t.speaker == exp_spk:
            correct += 1
    print(f"\n  说话人识别准确率: {correct}/{len(turns)} = {correct/max(len(turns),1)*100:.0f}%")
    if correct == len(turns):
        ok("上下文推断全部正确")
    elif correct >= len(turns) * 0.75:
        warn(f"上下文推断部分正确（{correct}/{len(turns)}）")
    else:
        fail(f"上下文推断准确率低（{correct}/{len(turns)}）")

    step("1.2 敬语剥离测试")
    # 中译文本通常用"同学/小姐"，日文原版用さん/くん
    honorific_cases = [
        ("八奈见同学", "八奈见"),
        ("温水君", "温水"),
        ("姬宫同学", "姬宫"),
        ("八奈見さん", "八奈見"),  # 日文原版
        ("綾野先輩", "綾野"),
    ]
    from src.domain.novel.dialogue import strip_honorific
    h_ok = 0
    for raw, exp in honorific_cases:
        got = strip_honorific(raw)
        m = "✓" if got == exp else "✗"
        print(f"    {m} '{raw}' → '{got}' (期望 '{exp}')")
        if got == exp:
            h_ok += 1
    print(f"\n  敬语剥离: {h_ok}/{len(honorific_cases)}")

    # ── 2. 端到端 ingest（MockEmbedding）──
    banner("Stage 2: 端到端 ingest（MockEmbedding）")
    from src.application.novel.ingest import ingest_novel
    from src.infrastructure.embedding import MockEmbeddingProvider
    from src.infrastructure.novel_store import NovelVectorStore

    tmp = str(ROOT / "data" / "_diag_jpln")
    shutil.rmtree(tmp, ignore_errors=True)
    store = NovelVectorStore(
        embedding=MockEmbeddingProvider(dimensions=1024),
        backend="lancedb", lance_path=tmp, dimensions=1024,
    )

    step("2.1 UTF-8 中译日轻")
    utf8_bytes = JAPANESE_LN_CN.encode("utf-8")
    try:
        r = await ingest_novel(utf8_bytes, "败北女角太多了.md", store=store, generate_qa=False)
        if r.success:
            ok(f"成功: ch={r.total_chapters} narr={r.narrative_blocks} "
               f"dial={r.dialogue_blocks} char={r.character_blocks}")
            print(f"         角色: {r.characters}")
        else:
            fail("失败", r.error)
    except Exception as e:
        fail("异常", f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}")

    step("2.2 GBK 中译日轻（验证编码修复在日轻场景同样生效）")
    gbk_bytes = JAPANESE_LN_CN.encode("gbk")
    try:
        r = await ingest_novel(gbk_bytes, "败北女角太多了gbk.txt", store=store, generate_qa=False)
        if r.success:
            ok(f"GBK 成功: narr={r.narrative_blocks} dial={r.dialogue_blocks} char={r.character_blocks}")
            print(f"         角色: {r.characters}")
        else:
            fail("GBK 失败", r.error)
    except Exception as e:
        fail("GBK 异常", f"{type(e).__name__}: {e}")

    step("2.3 Shift-JIS 日文原版（含假名，触发 JP 模式）")
    # 构造一段含假名的日文原版文本
    jp_original = """# 負けヒロイン

## 第一敗

八奈見は唇を噛み、顔を伏せた。

「もう言わないで。関係ないから。」

温水は彼女の横顔を見て、ため息をついた。

「俺はあのこと言ってない。誤解だよ。」

姫宮が軽い足取りで歩いてきた。

「二人は何してるの？放課後なのに帰らないの？」
"""
    try:
        sjis_bytes = jp_original.encode("shift_jis")
        r = await ingest_novel(sjis_bytes, "make_heroine.jp.txt", store=store, generate_qa=False)
        if r.success:
            ok(f"Shift-JIS 成功: narr={r.narrative_blocks} dial={r.dialogue_blocks}")
            print(f"         (JP 模式应已触发，因文本含假名)")
        else:
            fail("Shift-JIS 失败", r.error)
    except Exception as e:
        fail("Shift-JIS 异常", f"{type(e).__name__}: {e}")

    # ── 3. 角色发现质量分析 ──
    banner("Stage 3: 角色发现质量分析")
    step("3.1 期望角色 vs 实际发现")
    expected_chars = {"八奈见", "温水", "姬宫"}
    if r and r.success:
        # 用最后一次成功的 UTF-8 结果（重新跑一次确保）
        r_utf8 = await ingest_novel(utf8_bytes, "败北女角太多了_v2.md", store=store, generate_qa=False)
        actual = set(r_utf8.characters) if r_utf8.success else set()
        print(f"  期望角色: {expected_chars}")
        print(f"  实际发现: {actual}")
        found = expected_chars & actual
        missed = expected_chars - actual
        noise = actual - expected_chars
        print(f"  正确发现: {found}")
        if missed:
            warn(f"  漏检: {missed}")
        if noise:
            warn(f"  噪声角色: {noise}")
        if not missed and not noise:
            ok("角色发现完全正确")
        elif not missed:
            warn(f"无漏检，但有 {len(noise)} 个噪声角色")
        else:
            fail(f"漏检 {len(missed)} 个核心角色")

    shutil.rmtree(tmp, ignore_errors=True)
    banner("测试完成")


if __name__ == "__main__":
    asyncio.run(main())
