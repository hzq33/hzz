"""一键修正：用 alias.json 映射修正 LanceDB 里所有脏说话人"""
import lancedb, json, re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# 1. 加载所有 alias 映射（败犬 + Re0）
all_alias = {}
for series in ['败犬女主太多了', 'Re_从零开始的异世界生活']:
    try:
        alias_file = ROOT / 'data' / 'rosters' / f'{series}.alias.json'
        data = json.loads(alias_file.read_text(encoding='utf-8'))
        for e in data.get('entities', []):
            canon = e['canonical_name']
            all_alias[canon] = canon
            for a in e.get('aliases', []):
                if a and a != canon:
                    all_alias[a] = canon
    except: pass
print(f'alias map: {len(all_alias)} entries', flush=True)

# 2. 读 LanceDB 所有 dialogue blocks
con = lancedb.connect(str(ROOT / 'data' / 'novel_lance'))
t = con.open_table('novel_blocks')
import pandas as pd
df = t.to_pandas()
dialogue_mask = df['block_type'] == 'dialogue'
print(f'dialogue blocks: {dialogue_mask.sum()}', flush=True)

# 3. 统计修正前后
stats_before = defaultdict(int)
stats_after = defaultdict(int)
fixed_count = 0
dropped_count = 0
rows_to_update = []

for idx in df[dialogue_mask].index:
    row = df.loc[idx]
    try:
        turns = json.loads(row['dialogues_json']) if row['dialogues_json'] else []
    except:
        continue
    new_turns = []
    modified = False
    for turn in turns:
        sp = (turn.get('speaker') or '').strip()
        stats_before[sp] += 1

        # alias 映射
        fixed = all_alias.get(sp, sp)
        # 碎短语过滤：不在 alias 中 + 长度 > 6 或含助词 → 丢弃
        if fixed == sp and (len(sp) > 6 or any(kw in sp for kw in ['也','的','了','是','在','就','把','被','阁下','大人','阁下','与','而','且'])):
            fixed = None  # 标记丢弃
            dropped_count += 1
            modified = True
        elif fixed != sp:
            fixed_count += 1
            modified = True

        if fixed:
            turn['speaker'] = fixed
            stats_after[fixed] += 1
            new_turns.append(turn)

    if modified:
        rows_to_update.append((idx, json.dumps(new_turns, ensure_ascii=False)))

print(f'修正 {fixed_count} 条, 丢弃 {dropped_count} 条碎短语, {len(rows_to_update)} 块需更新', flush=True)

# 4. 预览修正
print('\n=== 修正样例 ===')
for sp, canon in all_alias.items():
    if sp != canon and stats_before.get(sp, 0) > 0:
        print(f'  {sp:<15} → {canon:<14} ({stats_before[sp]} 条)')
        if sum(1 for _ in all_alias.items()) > 12: break  # 只显示前几条

print('\n=== 丢弃的碎短语样例 ===')
showed = 0
for sp, cnt in sorted(stats_before.items(), key=lambda x: -x[1]):
    if all_alias.get(sp, sp) == sp and (len(sp) > 6 or any(kw in sp for kw in ['也','的','了','是'])):
        print(f'  ✗ {sp:<35} ({cnt} 条)')
        showed += 1
        if showed >= 10: break

# 5. 写回 LanceDB
if rows_to_update:
    # LanceDB 0.34: 逐个 update 或重建
    import pandas as pd
    from uuid import uuid4
    # 读全表 → 修改 → 重建（dialogues_json 列）
    all_df = t.to_pandas()
    for row_idx, new_json in rows_to_update:
        all_df.at[row_idx, 'dialogues_json'] = new_json
    # 重建表
    t.delete('1=1')  # 清空
    t.add(all_df)
    print(f'已写入 {len(rows_to_update)} 块', flush=True)
