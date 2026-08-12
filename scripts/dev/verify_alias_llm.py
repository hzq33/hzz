"""验证 alias_map 直接传 LLM 的对话归因效果.

用败犬女主太多了 vol01 前 5 章，只取第 1 章实际调 LLM，
打印 prompt 中的映射表 + LLM 输出的 speaker 名。
"""

import asyncio, json, os, sys
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))

from dotenv import load_dotenv
load_dotenv(encoding="utf-8-sig")

from src.shared.llm import SharedLLMClient
from src.domain.novel.dialogue_llm import LLMDialogueExtractor
from src.application.novel.dialogue_pipeline.tools import (
    assemble_prompt_candidates,
    build_alias_prompt_text,
)

MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

DOC_ID = "败犬女主太多了__vol01"
SERIES_ID = "败犬女主太多了"
MAX_CHAPTERS = 5


async def main():
    # ── 1. 加载 LanceDB narrative ──
    import lancedb
    db = lancedb.connect(str(ROOT / "data" / "novel_lance"))
    t = db.open_table("novel_blocks")
    df = t.to_pandas()
    mask = (df["doc_id"] == DOC_ID) & (df["block_type"] == "narrative")
    narr = df[mask].sort_values("global_id")
    chapters = narr.groupby("chapter_title")
    ch_names = list(chapters.groups.keys())
    print(f"找到 {len(ch_names)} 章，取前 {MAX_CHAPTERS} 章")
    # 过滤掉元数据章节（制作信息/插图/目录等），取有实质内容的章
    skip_patterns = ["制作信息", "插图", "目录", "彩页", "序章", "后记"]
    real_chapters = [
        (name, group) for name, group in chapters
        if name and name.strip() and not any(p in name for p in skip_patterns)
        and group["narrative_text"].str.len().sum() > 2000
    ]
    print(f"有效章节: {len(real_chapters)}，取前 {MAX_CHAPTERS} 章")
    real_chapters = real_chapters[:MAX_CHAPTERS]

    # ── 2. 构建映射表 ──
    alias_text = build_alias_prompt_text(SERIES_ID)
    print(f"\n映射表长度: {len(alias_text)} chars")
    print(f"映射表前 200 字符: {alias_text[:200]}...")

    # ── 3. 加载 volume seed (从 roster) ──
    from src.domain.novel.character_roster import load_roster
    roster = load_roster(SERIES_ID)
    volume_seed = [e.name for e in (roster.characters if roster else [])]
    print(f"Volume seed: {len(volume_seed)} names")

    # ── 4. LLM client ──
    llm = SharedLLMClient(
        primary={"model": MODEL, "api_key": API_KEY, "base_url": BASE_URL},
    )
    extractor = LLMDialogueExtractor(llm, max_tokens=4096, temperature=0.0)

    # ── 5. 对每章构建候选 + 调 LLM ──
    for ch_i, (ch_name, group) in enumerate(real_chapters):
        # 拼接 narrative text
        full_text = "\n".join(
            str(t) for t in group["narrative_text"].dropna().tolist()
        )
        full_text = full_text[:6000]  # 限长，节省 token

        # 构建候选（raw，不预清洗）
        from src.application.novel.dialogue_pipeline.harvest import harvest_chapter_names
        harvest = await harvest_chapter_names(full_text, llm, max_names=15, max_tokens=512)

        cands = assemble_prompt_candidates(
            volume_seed=volume_seed,
            chapter_text=full_text,
            spans=None,
            max_n=10,
            high_min=0.85,
            prefer_local=True,
            chapter_harvest=harvest,
            series_id=SERIES_ID,
        )

        print(f"\n{'='*60}")
        print(f"第 {ch_i+1} 章: {ch_name}")
        print(f"候选说话人 (raw): {cands}")

        # 只对第 1 章实际调 LLM，后面只打印候选
        if ch_i == 0:
            print(f"\n── LLM Prompt (user) ──")
            # 手动构造 prompt 看映射表是否传入
            cand_line = "、".join(cands[:20]) if cands else "（无）"
            prompt_parts = [f"章节：{ch_name}"]
            if alias_text:
                prompt_parts.append(f"【角色全名映射表（别名→全名）】{alias_text[:500]}...")
            prompt_parts.append(f"【候选说话人】{cand_line}")
            prompt_parts.append(f"\n【原文】\n{full_text[:500]}...")
            prompt_parts.append("\n请输出 JSON。")
            full_prompt = "\n".join(prompt_parts)
            print(full_prompt[:1500])

            print(f"\n── 调 LLM 中... ──")
            turns = await extractor.extract_window(
                full_text,
                chapter_title=ch_name,
                candidates=cands,
                max_tokens=4096,
                alias_map_text=alias_text,
            )

            print(f"\n── LLM 输出 ({len(turns)} turns) ──")
            for t in turns[:10]:
                sp = t.get("speaker", "?")
                content = t.get("content", "")[:50]
                conf = t.get("confidence", 0)
                print(f"  speaker={sp:12s} conf={conf:.2f}  content={content}")

            # 检查是否用了全名
            print(f"\n── 全名检查 ──")
            
            alias_text_full = build_alias_prompt_text(SERIES_ID)
            # 构建 canonical set
            canon_set = set()
            import re
            for pair in alias_text_full.split("，"):
                m = re.match(r".+→(.+)", pair)
                if m:
                    canon_set.add(m.group(1))
            print(f"  canonical 集合大小: {len(canon_set)}")
            for t in turns:
                sp = t.get("speaker", "?")
                in_canon = sp in canon_set
                status = "✅全名" if in_canon else ("⚠️别名/未知" if sp != "未知" else "  —未知")
                print(f"  {status}: {sp}")
        else:
            print(f"  (跳过 LLM 调用，仅展示候选)")

    await llm.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
