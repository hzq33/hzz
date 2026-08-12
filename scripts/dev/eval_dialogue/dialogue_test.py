"""抽败犬前2章验证alias映射效果"""
import lancedb, json, os, sys, asyncio, re, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
if (ROOT/".env").exists(): load_dotenv(ROOT/".env")

from src.domain.novel.dialogue_llm import LLMDialogueExtractor
from src.application.novel.dialogue_pipeline.tools import _load_alias_canonical_map

con = lancedb.connect(str(ROOT/"data/novel_lance"))
t = con.open_table("novel_blocks"); df = t.to_pandas()

# 败犬 vol01 前2章 narrative 文本
vol = df[(df['doc_id']=='败犬女主太多了__vol01') & (df['block_type']=='narrative')].head(200)
text = '\n'.join(str(tx) for tx in vol['narrative_text'] if tx)[:8000]
print(f"sample text: {len(text)} chars", flush=True)

async def run():
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
    class FakeLLM:
        def __init__(self): self.api_calls = 0
        async def achat(self, messages, temperature=0.0, max_tokens=4096, **kw):
            resp = await client.chat.completions.create(
                model="deepseek-chat", messages=messages,
                temperature=temperature, max_tokens=max_tokens,
            )
            self.api_calls += 1
            return resp.choices[0].message.content
    llm = FakeLLM()
    extractor = LLMDialogueExtractor(llm)

    # 只用干净名单构造 candidates（不做 text harvest——验证 alias 映射效果）
    alias_map = _load_alias_canonical_map('败犬女主太多了')
    seed_names = [c['canonical_name'] for c in json.load(open('data/rosters/败犬女主太多了.alias.json',encoding='utf-8'))['entities'] if c.get('importance') in ('main','supporting')]
    # 找出章节文本中实际出现的名字
    cands = [n for n in seed_names if n in text or any(a in text for a in [k for k,v in alias_map.items() if v==n])]
    cands = cands[:15]
    print(f"candidates: {cands}", flush=True)

    t0 = time.time()
    turns = await extractor.extract_window(text, chapter_title="vol01 开头", candidates=cands)
    print(f"\nLLM extract: {time.time()-t0:.0f}s, {len(turns)} turns", flush=True)
    print("\n=== 抽取的对话 ===")
    for t in turns:
        sp = t.get('speaker','?')
        # alias映射修正
        sp_fixed = alias_map.get(sp, sp)
        mark = " ✓" if sp_fixed != sp else ""
        print(f"  [{sp_fixed:<12}] {t.get('content','')[:60]}{mark}")

asyncio.run(run())
