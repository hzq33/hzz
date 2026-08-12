"""验证：build_character_inventory → alias.json 直接落盘"""
import asyncio, json, os, sys, time, lancedb
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
if (ROOT/".env").exists(): load_dotenv(ROOT/".env")
from src.domain.novel.character_inventory.builder import build_character_inventory

# 1. 构造 document
con = lancedb.connect(str(ROOT/"data/novel_lance"))
t = con.open_table("novel_blocks"); df = t.to_pandas()
class Ch: __init__ = lambda s,tit,txt: setattr(s,'title',tit) or setattr(s,'text',txt)
class Doc: chapters = []
chapters = []
for d in sorted(set(d for d in df['doc_id'] if d.startswith('败犬'))):
    sub = df[(df['doc_id']==d) & (df['block_type']=='narrative')]
    text = '\n'.join(str(tx) for tx in sub['narrative_text'] if tx)
    chapters.append(type('Ch',(),{'title':d,'text':text}))
doc = type('Doc',(),{'chapters':chapters})()
print(f"document: {len(chapters)} vols, {sum(len(c.text) for c in chapters)} chars", flush=True)

# 2. LLM client
from openai import AsyncOpenAI
async_client = AsyncOpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
class LLM:
    async def achat(self, messages, temperature=0.0, max_tokens=4096, **kw):
        resp = await async_client.chat.completions.create(
            model="deepseek-chat", messages=messages, temperature=temperature, max_tokens=max_tokens)
        return resp.choices[0].message.content

async def main():
    t0 = time.time()
    result = await build_character_inventory(
        doc, series_id="败犬女主太多了", llm_client=LLM(),
        config={"device":"cuda","ner_min_conf":0.3,"max_clusters_for_llm":60,"max_chars":0})
    print(f"done {time.time()-t0:.0f}s, {len(result.characters)} characters", flush=True)
    # 读 alias.json
    alias = json.loads(Path("data/rosters/败犬女主太多了.alias.json").read_text(encoding='utf-8'))
    print(f"\nalias.json: {alias['meta']['total']} entities")
    for e in alias['entities']:
        print(f"  {e['canonical_name']:<18} {e['importance']:<12} aliases={e['aliases'][:6]}")

asyncio.run(main())
