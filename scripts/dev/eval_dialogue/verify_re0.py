"""Re0 验证：build_character_inventory → alias.json"""
import asyncio, json, os, sys, time, lancedb
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
if (ROOT/".env").exists(): load_dotenv(ROOT/".env")
from src.domain.novel.character_inventory.builder import build_character_inventory

con = lancedb.connect(str(ROOT/"data/novel_lance"))
t = con.open_table("novel_blocks"); df = t.to_pandas()
chapters = []
for d in sorted(set(x for x in df['doc_id'] if x.startswith('Re：'))):
    sub = df[(df['doc_id']==d) & (df['block_type']=='narrative')]
    text = '\n'.join(str(tx) for tx in sub['narrative_text'] if tx)
    chapters.append(type('Ch',(),{'title':d,'text':text}))
doc = type('Doc',(),{'chapters':chapters})()
print(f"Re0: {len(chapters)} vols, {sum(len(c.text) for c in chapters)} chars", flush=True)

from openai import AsyncOpenAI
client = AsyncOpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
class LLM:
    async def achat(self, messages, temperature=0.0, max_tokens=4096, **kw):
        resp = await client.chat.completions.create(
            model="deepseek-chat", messages=messages, temperature=temperature, max_tokens=max_tokens)
        return resp.choices[0].message.content

async def main():
    t0 = time.time()
    result = await build_character_inventory(
        doc, series_id="Re：从零开始的异世界生活", llm_client=LLM(),
        config={"device":"cuda","ner_min_conf":0.3,"max_clusters_for_llm":60,"max_chars":0})
    elapsed = time.time()-t0
    print(f"done {elapsed:.0f}s, {result.draft_clusters} clusters, {len(result.characters)} chars, {result.llm_calls} llm_calls", flush=True)

    alias = json.loads(Path("data/rosters/Re_从零开始的异世界生活.alias.json").read_text(encoding='utf-8'))
    print(f"\nalias.json: {len(alias['entities'])} entities")
    for e in alias['entities']:
        print(f"  {e['canonical_name']:<20} {e['importance']:<12} aliases={e['aliases'][:8]}")

asyncio.run(main())
