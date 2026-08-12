"""L1-fast: Re0 vol35 NER+聚类 → LLM 归一 → 名单"""
import lancedb, collections, time, json, re, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
if (ROOT/".env").exists(): load_dotenv(ROOT/".env")

# 1. 抽文本
con = lancedb.connect(str(ROOT/"data"/"novel_lance"))
t = con.open_table("novel_blocks"); df = t.to_pandas()
text = '\n'.join(str(tx) for tx in df[(df['doc_id'].str.startswith('Re：')) & (df['block_type']=='narrative')]['narrative_text'] if tx)
print(f"Re0 text: {len(text)} chars", flush=True)

# 2. NER + 聚类
from src.domain.novel.character_ner import extract_person_mentions, cluster_mentions
t0 = time.time(); mentions = extract_person_mentions(text, device='cpu', min_conf=0.3)
clusters = cluster_mentions(mentions, min_mentions=2, text=text)
print(f"NER: {len(mentions)} mentions, {len(clusters)} clusters ({time.time()-t0:.0f}s)", flush=True)

# 3. 方案 I prompt
SYSTEM="""你是轻小说角色归一器。一次处理全部候选人名簇，输出完整角色表。
规则：
1. 去噪:删除作家/名人/作者/编辑/身份词/碎片/单字
2. 合并:简称+全名→canonical选最长形式
3. 拆开:共享姓/字≠同一人(如"温水和彦"≠"温水佳树")
4. 全名:canonical必须从evidence/surfaces中验证
5. 禁止幻觉
6. 只输出JSON:{"characters":[{"canonical_name":"...","aliases":[...],"importance":"main|supporting|extra","from_clusters":[...]}],"dropped":[{"from_clusters":[...],"reason":"..."}]}"""

payload=[{"id":c.cluster_id,"surfaces":c.surfaces[:8],"count":c.count,"evidence":c.evidence[:8]} for c in clusters]
user=json.dumps({"series":"Re:从零开始的异世界生活","clusters":payload},ensure_ascii=False)
print(f"prompt: {len(user)} chars", flush=True)

# 4. LLM
from openai import OpenAI
client=OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),base_url="https://api.deepseek.com/v1")
t1=time.time()
resp=client.chat.completions.create(model="deepseek-chat",messages=[{"role":"system","content":SYSTEM},{"role":"user","content":user}],temperature=0,max_tokens=8000)
raw=resp.choices[0].message.content or ""
print(f"LLM: {time.time()-t1:.0f}s, {len(raw)} chars", flush=True)

# 5. 解析
data=json.loads(raw) if raw.strip().startswith("{") else json.loads(re.search(r"\{.*\}",raw,re.DOTALL).group())
print(f"\n=== Re0 角色表 ({len(data.get('characters',[]))} 角色, {len(data.get('dropped',[]))} 删除) ===")
for c in data.get("characters",[]):
    print(f"  {c['canonical_name']:<14} {c.get('importance','?'):<10} aliases={c.get('aliases',[])}")
print("\n=== dropped ===")
for d in data.get("dropped",[]): print(f"  {d}")
