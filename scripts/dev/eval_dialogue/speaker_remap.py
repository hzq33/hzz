"""用方案I干净名单修正库里脏说话人 — 子串匹配"""
import lancedb, json, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# 1. 加载干净名单（从 L0/L1 结果合并）
canonical_to_aliases = {}
# 败犬
bai = json.load(open(str(ROOT/"scripts/dev/eval_dialogue/data/_l0_llm_result.json"),encoding='utf-8'))
for c in bai['parsed']['characters']:
    name=c['canonical_name']
    canonical_to_aliases[name]=set(c.get('aliases',[]))|{name}
# 败犬补充已知角色
canonical_to_aliases.setdefault('甘夏古奈美',set()).add('甘夏老师')
canonical_to_aliases['八奈见杏菜'].add('八奈见')
canonical_to_aliases['温水和彦'].add('温水')
canonical_to_aliases['温水佳树'].add('佳树')
canonical_to_aliases['烧盐柠檬'].add('烧盐')
canonical_to_aliases['朝云千早'].add('朝云')
canonical_to_aliases['志喜屋梦子'].add('志喜屋')
canonical_to_aliases['月之木古都'].add('月之木')
canonical_to_aliases['绫野光希'].add('绫野')
canonical_to_aliases['姬宫华恋'].add('华恋')
canonical_to_aliases['袴田草介'].add('草介')
canonical_to_aliases['玉木慎太郎'].add('慎太郎')
canonical_to_aliases['小鞠知花'].add('小鞠')
canonical_to_aliases.setdefault('小拔小夜',set()).update(['小抜','小拔'])
canonical_to_aliases.setdefault('马剃天爱星',set()).add('天爱星')
canonical_to_aliases.setdefault('放虎原阳叶里',set()).add('放虎原')

# Re0
re0_data = None
try:
    log=open(ROOT/"scripts/dev/eval_dialogue/data/_l1_re0.log",encoding='utf-8').read()
    for line in log.split('\n'):
        if line.startswith('  ') and 'importance=' in line:
            parts=line.strip().split()
            if len(parts)>=3:
                name=parts[0]
                canonical_to_aliases.setdefault(name,set()).add(name)
except: pass

# 2. 读库里所有说话人
con=lancedb.connect(str(ROOT/"data/novel_lance"))
t=con.open_table("novel_blocks"); df=t.to_pandas()
all_speakers=collections.Counter()
for dj in df['dialogues_json']:
    try:
        for x in (json.loads(dj) if dj else []):
            s=(x.get('speaker')or'').strip()
            if s: all_speakers[s]+=1
    except: pass

# 3. 匹配
matched={}
for sp,count in all_speakers.most_common():
    best=None; best_len=0
    for canon,aliases in canonical_to_aliases.items():
        for alias in aliases:
            if alias and alias in sp:
                if len(alias)>best_len:
                    best_len=len(alias); best=canon
    if best:
        matched[sp]=(best,count)

total_turns=sum(all_speakers.values())
matched_turns=sum(c for _,c in matched.values())
print(f"Total: {len(all_speakers)} speakers, {total_turns} turns")
print(f"Matched: {len(matched)} speakers, {matched_turns} turns ({matched_turns/total_turns:.1%})")
print()
for sp,(canon,count) in sorted(matched.items(),key=lambda x:-x[1][1])[:40]:
    print(f"  {sp:<30} → {canon:<14} ({count} turns)")
print()
unmatched=[(sp,c) for sp,c in all_speakers.most_common() if sp not in matched]
print(f"=== Unmatched {len(unmatched)} speakers ===")
for sp,c in unmatched[:30]:
    print(f"  {sp:<35} ({c} turns)")
