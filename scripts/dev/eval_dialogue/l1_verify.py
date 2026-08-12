"""L1 验证：用败犬 vol01-03 数据跑方案 I 的 build_character_inventory"""
from __future__ import annotations

import asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

import lancedb
from src.domain.novel.character_ner import DraftCluster

# 1. 从 LanceDB 构造 document
con = lancedb.connect(str(ROOT / "data" / "novel_lance"))
t = con.open_table("novel_blocks")
df = t.to_pandas()
docs = sorted(set(df["doc_id"]))

# 构造简单 document（含 chapters）
class FakeChapter:
    def __init__(self, title, text):
        self.title = title; self.text = text

class FakeDoc:
    def __init__(self, chapters): self.chapters = chapters

chapters = []
for d in docs:
    if "败犬" not in d: continue
    sub = df[(df["doc_id"] == d) & (df["block_type"] == "narrative")]
    text = "\n".join(str(t) for t in sub["narrative_text"] if t)
    chapters.append(FakeChapter(d, text))

document = FakeDoc(chapters)
print(f"document: {len(chapters)} vols, {sum(len(c.text) for c in chapters)} chars")

# 2. 构造 LLM client
from openai import AsyncOpenAI
client = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
)

class FakeLLM:
    def __init__(self, client, model="deepseek-chat"):
        self.client = client
        self.model = model
    async def achat(self, messages, temperature=0.0, max_tokens=4096, **kw):
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content

llm = FakeLLM(client)

# 3. 跑 inventory
from src.domain.novel.character_inventory.builder import build_character_inventory

async def main():
    t0 = time.time()
    result = await build_character_inventory(
        document,
        series_id="败犬女主太多了",
        llm_client=llm,
        config={"enabled": True, "max_chars": 0, "device": "cpu", "ner_min_conf": 0.3, "max_clusters_for_llm": 60},
    )
    elapsed = time.time() - t0
    print(f"elapsed: {elapsed:.0f}s, llm_skipped={result.llm_skipped}, llm_calls={result.llm_calls}")
    print(f"clusters: {result.draft_clusters}, kept: {len(result.characters)}, dropped: {len(result.dropped)}")
    print()
    print("=== 角色表 (main/supporting) ===")
    for c in result.characters:
        if c.importance in ("main", "supporting"):
            print(f"  {c.canonical_name:<14} {c.importance:<12} mentions={c.mention_count} aliases={c.aliases[:6]}")
    print()
    print("=== extra ===")
    for c in result.characters:
        if c.importance == "extra":
            print(f"  {c.canonical_name:<14} mentions={c.mention_count} aliases={c.aliases[:4]}")
    print()
    print("=== dropped ===")
    for d in result.dropped:
        print(f"  {d}")

asyncio.run(main())
