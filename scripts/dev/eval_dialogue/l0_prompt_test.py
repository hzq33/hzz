"""L0: 方案 I prompt 验证 — 56 簇全喂 LLM + evidence, 输出角色表。"""
from __future__ import annotations

import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

clusters = json.loads(
    (ROOT / "scripts/dev/eval_dialogue/data/_l0_clusters.json").read_text(
        encoding="utf-8")
)

SYSTEM = """你是轻小说角色归一器。一次处理全部候选人名簇，输出完整、无重复的角色表。

核心规则：
1. 去噪 — 删除：
   - 文中引用的作家/名人（芥川、太宰治、三岛由纪夫、村上春树、拿破仑、赫敏、麦凯恩、小田和正、川端）
   - 作者/编辑/图源等元信息名（雨森、岩浅氏、小村雏）
   - 职业/身份词（店员/部长/老师——除非是唯一指代）
   - 碎片（单字、纯数字/标点、无意义词）

2. 合并 — 同一人的多个簇合并：
   - 简称+全名（c1 八奈见 + c27 八奈见杏菜 → canonical=八奈见杏菜）
   - 姓碎片+全名（c3 绫野 + c14 绫野光希 → canonical=绫野光希）
   - 只要有 evidence 支持就合并
   - 合并后 canonical 选 surfaces 或 evidence 中最长、最正式的形式
   - 简称/碎片/称呼变体进 aliases

3. 拆开（关键）— 不同人即使共享字或姓，不得合并：
   - c35 温水和彦 与 c25 温水佳树 是两个人（兄妹）
   - c46 月之木古都 与 c30 玉木慎太郎 是两个人（"玉木"是姓的错拼或不同人物）
   - c7 佳树(canonical=温水佳树) 中的"春树"是错字，不计入 alias
   - 拿不准就拆成两条

4. 全名选择（关键）— canonical_name 必须是 evidence/surfaces 中最长、最正式的形式：
   - 如果 evidence/surfaces 中有"温水和彦"和"温水"，选"温水和彦"
   - c15 田草介/草介 → 从 evidence 中找"袴田草介"（文本里有），选"袴田草介"
   - canonical 必须能从 evidence 或 surfaces 中验证

5. 别名收录 — 简称/称呼变体/错字 → 进 aliases：
   - "温水""温温""温水君"是温水和彦的 aliases
   - "八奈""八奈见""奈美""奈见"是八奈见杏菜的 aliases
   - "春树"是错字（佳树的错识别），不计入 alias
   - 拿不准 → 只保留标准简称

6. 禁止幻觉：没有 evidence 不要编造全名。拿不准 → importance=extra。

7. 只输出 JSON：
{
  "characters": [
    {
      "canonical_name": "八奈见杏菜",
      "aliases": ["八奈", "八奈见"],
      "importance": "main",
      "from_clusters": ["c1", "c27"],
      "evidence_for_canonical": "c27 evidence或surfaces中包含'八奈见杏菜'"
    }
  ],
  "dropped": [
    {"from_clusters": ["c9"], "reason": "太宰治是文中引用的作家,非角色"}
  ]
}
"""

USER = json.dumps(
    {"series": "败犬女主太多了", "total_clusters": len(clusters["clusters"]),
     "clusters": clusters["clusters"]},
    ensure_ascii=False,
)

print(f"clusters: {len(clusters['clusters'])}, user prompt: {len(USER)} chars",
      flush=True)

# --- DeepSeek call ---
import os, time
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
)
model = "deepseek-chat"

t0 = time.time()
resp = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER + "\n\n请输出 JSON。角色的 canonical_name 必须是 evidence 或 surfaces 中出现的最长完整人名。作家/作者/名人名（芥川、太宰治、三岛由纪夫、村上春树、拿破仑、赫敏、雨森、岩浅氏、小村雏）必须列入 dropped。"},
    ],
    temperature=0.0,
    max_tokens=8000,
)
raw = resp.choices[0].message.content or ""
elapsed = time.time() - t0
print(f"LLM {elapsed:.0f}s, {len(raw)} chars output", flush=True)

# Parse JSON
import re
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    data = json.loads(m.group()) if m else {}

print("\n=== 角色表 ===")
for c in data.get("characters", []):
    print(
        f"  {c.get('canonical_name','?'):<12} importance={c.get('importance','?')} aliases={c.get('aliases')} from_clusters={c.get('from_clusters')}")

print("\n=== 删除 ===")
for d in data.get("dropped", []):
    print(f"  {d.get('from_clusters')} | {d.get('reason')}")

# 保存结果
out_path = ROOT / "scripts/dev/eval_dialogue/data/_l0_llm_result.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"elapsed": elapsed, "raw": raw, "parsed": data},
              f, ensure_ascii=False, indent=1)
print(f"\nsaved: {out_path}")
