"""四通道功能验证脚本 — 验证 NOVEL_RAG_CHANNELS.md 描述的各通道。

验证内容:
  Stage 1: ingest 测试小说，检查 4 通道数据分布
  Stage 2: narrative 通道 — 场景描写搜索
  Stage 3: dialogue 通道 — 对话风格搜索
  Stage 4: qa 通道 — 事实查证搜索（需 LLM，可能为空）
  Stage 5: character 通道 — 角色档案搜索
  Stage 6: 跨通道加权融合 search_multi
  Stage 7: 存储格式验证（LanceDB schema + 向量维度）
"""

import asyncio
import os
import sys
import shutil
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 尝试加载 .env（可能 localhost:9527 不可用，ingest 会容错跳过 QA）
from dotenv import load_dotenv
_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)


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


async def main():
    banner("Novel RAG 四通道功能验证")
    print(f"Python: {sys.version.split()[0]}")
    print(f"DEEPSEEK_API_KEY: {'已设置' if os.getenv('DEEPSEEK_API_KEY') else '未设置'}")
    print(f"DEEPSEEK_BASE_URL: {os.getenv('DEEPSEEK_BASE_URL', '默认')}")

    # ── 准备：ingest 测试小说 ──
    tmp_lance = str(ROOT / "data" / "_channel_verify")
    shutil.rmtree(tmp_lance, ignore_errors=True)

    from src.application.novel.factory import create_novel_store
    from src.application.novel.ingest import ingest_novel

    step("准备：创建 Qwen3 store（GPU 加速）")
    store = create_novel_store(backend="lancedb", lance_path=tmp_lance)

    test_novel = ROOT / "data" / "测试小说.md"
    if not test_novel.exists():
        fail("测试小说不存在", str(test_novel))
        return

    has_llm = bool(os.getenv("DEEPSEEK_API_KEY"))
    step(f"ingest 测试小说《{test_novel.stem}》（generate_qa={has_llm}）")
    import time
    t0 = time.time()
    result = await ingest_novel(
        test_novel.read_bytes(), test_novel.name,
        store=store, generate_qa=has_llm,
    )
    dt = time.time() - t0
    if not result.success:
        fail("ingest 失败", result.error)
        return
    ok(f"ingest 成功 {dt:.1f}s")
    print(f"    chapters={result.total_chapters} narr={result.narrative_blocks} "
          f"dial={result.dialogue_blocks} qa={result.qa_blocks} char={result.character_blocks}")
    print(f"    characters: {result.characters}")

    # ── Stage 1: 通道数据分布 ──
    banner("Stage 1: 通道数据分布")
    from collections import Counter
    import lancedb
    db = lancedb.connect(tmp_lance)
    tbl = db.open_table("novel_blocks")
    rows = tbl.to_arrow().to_pylist()
    type_counts = Counter(r["block_type"] for r in rows)
    print(f"  总 blocks: {len(rows)}")
    for bt in ["narrative", "dialogue", "qa", "character"]:
        cnt = type_counts.get(bt, 0)
        status = "OK" if cnt > 0 else "EMPTY"
        print(f"    {bt:12}: {cnt:3} [{status}]")

    # 检查每个通道的向量列是否有值
    vec_cols = ["vec_narrative", "vec_dialogue", "vec_qa", "vec_character"]
    for vcol in vec_cols:
        non_zero = sum(1 for r in rows if r.get(vcol) and any(x != 0 for x in r[vcol]))
        print(f"    {vcol:16}: {non_zero}/{len(rows)} 非零向量")

    # ── Stage 2: narrative 通道 ──
    banner("Stage 2: narrative 通道 — 场景描写搜索")
    narrative_queries = [
        ("镜湖的景色描写", "应返回含'夜色如墨/镜湖/柳树'的叙事块"),
        ("林晚晴第一次遇到顾清寒", "应返回第一章初遇场景"),
        ("剑谱", "应返回提及镜湖剑谱的叙事"),
    ]
    for query, expectation in narrative_queries:
        step(f"搜索: '{query}'")
        results = await store.search(query, channel="narrative", top_k=3)
        if results:
            top = results[0]
            text = top.block.narrative_text or ""
            print(f"    top1 score={top.score:.4f} ch='{top.block.chapter_title}'")
            print(f"    text: {text[:80]}...")
            if any(kw in text for kw in ["镜湖", "夜色", "柳树", "剑谱", "林晚晴", "顾清寒"]):
                ok(f"命中关键词，{expectation}")
            else:
                warn(f"未命中预期关键词")
        else:
            fail("无结果")

    # ── Stage 3: dialogue 通道 ──
    banner("Stage 3: dialogue 通道 — 对话风格搜索")
    dialogue_queries = [
        ("林晚晴 清冷 语气", "应返回林晚晴的对话"),
        ("顾清寒 拱手", "应返回顾清寒的对话"),
        ("你是什么人", "应匹配该对话内容"),
    ]
    for query, expectation in dialogue_queries:
        step(f"搜索: '{query}'")
        results = await store.search(query, channel="dialogue", top_k=3)
        if results:
            top = results[0]
            dialogues = top.block.dialogues or []
            speakers = [d.speaker for d in dialogues]
            contents = [d.content for d in dialogues]
            print(f"    top1 score={top.score:.4f} speakers={speakers}")
            print(f"    contents: {contents[:2]}")
            if any(query[:3] in c or query[:3] in s for c, s in zip(contents, speakers)):
                ok(f"命中，{expectation}")
            elif speakers:
                ok(f"返回对话（speakers={speakers}）")
            else:
                warn("对话内容为空")
        else:
            fail("无结果")

    # ── Stage 4: qa 通道 ──
    banner("Stage 4: qa 通道 — 事实查证搜索")
    if type_counts.get("qa", 0) == 0:
        warn("QA 通道为空（LLM 不可用或未生成 QA）")
        warn("需 DEEPSEEK_API_KEY 且 base_url 服务运行才能生成 QA")
    else:
        qa_queries = ["林晚晴是谁", "镜湖山庄的庄主", "剑谱是什么"]
        for query in qa_queries:
            step(f"搜索: '{query}'")
            results = await store.search(query, channel="qa", top_k=2)
            if results:
                top = results[0]
                print(f"    Q: {top.block.question}")
                print(f"    A: {top.block.answer[:80] if top.block.answer else '(空)'}")
                ok(f"score={top.score:.4f}")
            else:
                warn(f"无结果")

    # ── Stage 5: character 通道 ──
    banner("Stage 5: character 通道 — 角色档案搜索")
    step("list_characters()")
    all_chars = store.list_characters()
    print(f"    所有角色: {all_chars}")

    char_queries = [
        ("林晚晴的性格", "林晚晴"),
        ("顾清寒 说话风格", "顾清寒"),
        ("沈墨", "沈墨"),
    ]
    for query, expected_name in char_queries:
        step(f"搜索: '{query}'")
        results = await store.search(query, channel="character", top_k=3)
        if results:
            top = results[0]
            b = top.block
            print(f"    top1: name='{b.character_name}' score={top.score:.4f}")
            print(f"    personality: {b.personality or '(空)'}")
            print(f"    speaking_style: {b.speech_style or '(空)'}")
            if b.character_name == expected_name:
                ok(f"正确命中 {expected_name}")
            else:
                warn(f"期望 {expected_name}，实际 {b.character_name}")
        else:
            fail("无结果")

    # ── Stage 6: 跨通道加权融合 ──
    banner("Stage 6: 跨通道加权融合 search_multi")
    multi_query = "林晚晴的性格和说话方式"
    step(f"search_multi: '{multi_query}'")
    weights = {
        "character": 1.0,
        "qa": 0.3,
        "dialogue": 0.2,
        "narrative": 0.5,
    }
    print(f"    weights: {weights}")
    results = await store.search_multi(multi_query, channel_weights=weights, top_k=5)
    if results:
        print(f"    返回 {len(results)} 条融合结果:")
        channels_hit = set()
        for i, r in enumerate(results):
            channels_hit.add(r.channel)
            print(f"      {i+1}. [{r.channel:9}] score={r.score:.4f} "
                  f"doc={r.block.doc_id} type={r.block.block_type}")
        ok(f"融合成功，命中 {len(channels_hit)} 个通道: {channels_hit}")
        if len(channels_hit) >= 2:
            ok("跨通道融合生效（≥2 通道命中）")
        else:
            warn(f"仅 {len(channels_hit)} 通道命中，融合效果有限")
    else:
        fail("融合无结果")

    # ── Stage 7: 存储格式验证 ──
    banner("Stage 7: 存储格式验证（LanceDB schema）")
    step("schema 检查")
    schema = tbl.schema
    expected_cols = [
        "global_id", "doc_id", "block_type", "chapter_title",
        "narrative_text", "dialogues_json", "question", "answer",
        "character_name", "personality", "speech_style",
        "vec_narrative", "vec_dialogue", "vec_qa", "vec_character",
    ]
    actual_cols = [f.name for f in schema]
    missing = [c for c in expected_cols if c not in actual_cols]
    if missing:
        fail(f"schema 缺失列: {missing}")
    else:
        ok(f"schema 完整（{len(expected_cols)} 列）")

    step("向量维度检查")
    sample = rows[0] if rows else {}
    for vcol in vec_cols:
        vec = sample.get(vcol, [])
        dim = len(vec)
        status = "OK" if dim == 1024 else f"WRONG({dim})"
        print(f"    {vcol:16}: dim={dim} [{status}]")

    step("doc_id 隔离检查")
    doc_ids = set(r["doc_id"] for r in rows)
    print(f"    doc_ids: {doc_ids}")
    # 搜索时带 doc_id 过滤
    if doc_ids:
        test_doc = list(doc_ids)[0]
        results = await store.search("测试", channel="narrative", doc_id=test_doc, top_k=3)
        all_match = all(r.block.doc_id == test_doc for r in results)
        if all_match:
            ok(f"doc_id 过滤生效（仅返回 {test_doc}）")
        else:
            fail("doc_id 过滤失效")

    # 清理
    shutil.rmtree(tmp_lance, ignore_errors=True)
    banner("验证完成")


if __name__ == "__main__":
    asyncio.run(main())
