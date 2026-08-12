"""L0: LLM 对话样本筛选 — 按角色精选代表性对话"""
import lancedb, json, os, sys, re, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
if (ROOT/".env").exists(): load_dotenv(ROOT/".env")

# 1. 读库 + 按角色聚合
con = lancedb.connect(str(ROOT/"data/novel_lance"))
t = con.open_table("novel_blocks"); df = t.to_pandas()
from collections import defaultdict
by_char = defaultdict(list)
for _, row in df[df['block_type']=='dialogue'].iterrows():
    try:
        for turn in (json.loads(row['dialogues_json']) if row.get('dialogues_json') else []):
            sp = (turn.get('speaker') or '').strip()
            content = (turn.get('content') or '').strip()
            if sp and sp != '未知' and len(sp) <= 10 and len(content) >= 4:
                by_char[sp].append(content)
    except: pass

# 2. 挑样本最多的角色
targets = sorted(by_char.items(), key=lambda x: -len(x[1]))[:4]
# 手动补全 alias（库里简称 → 全名）
alias_fix = {'八奈见':'八奈见杏菜','温水':'温水和彦','烧盐':'烧盐柠檬','甘夏':'甘夏古奈美',
             '佳树':'温水佳树','天爱星':'马剃天爱星','绫野':'绫野光希','月之木':'月之木古都',
             '志喜屋':'志喜屋梦子','华恋':'姬宫华恋','田草介':'袴田草介','草介':'袴田草介',
             '小鞠':'小鞠知花','朝云':'朝云千早'}

SYSTEM="""你是轻小说角色对话样本筛选器。
从给定角色的所有台词中，选出 8-12 条最能体现该角色**独特说话风格**的代表性对话。

筛选标准（按重要性排序）：
1. **角色辨识度** — 换个角色不会这么说（特有的口癖、称谓、句式）
2. **情绪信号** — 有明显的感叹/反问/省略/语气词（！？！…～呢吧嘛啊噢呀），不是平铺直叙
3. **场景多样性** — 覆盖不同情绪（开心/愤怒/悲伤/吐槽/日常），不要全是同类对话
4. **长度适中** — 优先 10-40 字，纯应答（"嗯""好""是的"）和超短句跳过

输出 JSON：
{
  "character": "角色名",
  "samples": [
    {"index": 3, "content": "台词原文", "reason": "为什么选这条（1句话）"},
    ...
  ]
}
"""

from openai import OpenAI
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")

for raw_name, lines in targets:
    # alias 修正
    name = alias_fix.get(raw_name, raw_name)
    # 去重 + 最多取 80 条喂 LLM
    seen = set(); deduped = []
    for l in lines:
        key = l[:30]
        if key not in seen:
            seen.add(key); deduped.append(l)
    deduped = deduped[:80]

    numbered = "\n".join(f"{i}. {l}" for i, l in enumerate(deduped, 1))
    user = f"角色：{name}\n台词（共{len(deduped)}条）：\n{numbered}"

    t0 = time.time()
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role":"system","content":SYSTEM},{"role":"user","content":user}],
        temperature=0.0, max_tokens=3000,
    )
    raw = resp.choices[0].message.content or ""
    elapsed = time.time() - t0

    # 解析
    try:
        data = json.loads(raw)
    except:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group()) if m else {}

    print(f"\n## {name} ({len(deduped)} 句 → {len(data.get('samples',[]))} 精选, {elapsed:.0f}s)")
    for s in data.get("samples", []):
        print(f"  [{s.get('index')}] {s.get('content','')[:70]}")
        if s.get('reason'):
            print(f"      → {s['reason']}")
