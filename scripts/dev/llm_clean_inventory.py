# -*- coding: utf-8 -*-
"""LLM 清洗已落盘候选名单（对比规则清洗效果）"""
import asyncio, json, sys
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", encoding="utf-8-sig")

SERIES = "关于我转生变成史莱姆这档事"

SYSTEM = """你是小说角色名单清洗器。给定一部小说的候选角色名单（名字 + 提及次数 + 别名），
对每个候选裁决：【保留】真实角色 / 【保留技能】会说话的技能或能力（如史莱姆的「大贤者」
「捕食者」——它们有台词会说话）/ 【删除】泛称（商人/勇者/国家/城镇/蘑菇）、种族（矮人/人类）、
地名（矮人王国）、普通物品（回复药/火焰短剑）、作者/注释人物 / 【合并】同一角色的重复条目。

规则：
- "角色"：有名字的具体人物（说话/被提及）→ keep
- "技能"：会说话的技能/能力/系统音（大贤者/捕食者/魔法筒/焰之巨人/钢丝/水刀/魔力感知等）→ keep_skills
- 泛称/种族/地名/物品/作者 → remove（给出 reason）
- 明显同一人（凯金+凯多 是矮人王同人）→ merge 到 canonical
- 拿不准 → keep（宁可多留，extra 级无害）

输出 JSON：
{"keep": ["凯金", "利姆露"], "keep_skills": ["大贤者"], "remove": [{"name": "商人", "reason": "泛称"}], "merge": [{"canonical": "凯金", "names": ["凯金", "凯多"]}]}"""


async def main():
    from src.shared.llm import SharedLLMClient
    from src.utils.config import load_config
    from src.shared.llm_config import get_endpoint_config

    # 用 DeepSeek 默认配置（agent）
    cfg = load_config(str(ROOT / "config.yaml"))
    ep = get_endpoint_config("character_inventory")
    llm = SharedLLMClient(
        primary={
            "model": ep.get("model") or "deepseek-v4-pro",
            "api_key": ep.get("api_key") or cfg.get("agent", {}).get("api_key", ""),
            "base_url": ep.get("base_url") or "https://api.deepseek.com",
        },
        temperature=0.0,
        max_tokens=4096,
    )

    inv_path = ROOT / "data" / "inventories" / f"{SERIES}.json"
    bak_path = Path(str(inv_path) + ".bak")  # 规则清洗前的原始 125
    src = bak_path if bak_path.exists() else inv_path
    data = json.loads(src.read_text(encoding="utf-8"))
    cands = data.get("candidates") or []
    print(f"输入候选数: {len(cands)} (来源: {src.name})")

    lines = []
    for c in sorted(cands, key=lambda x: -x.get("mention_count", 0)):
        al = ",".join(c.get("aliases", [])[:3])
        lines.append(f"- {c.get('name','')} (mention={c.get('mention_count',0)}, aliases=[{al}])")
    payload = "\n".join(lines)

    import time
    t0 = time.time()
    raw = await llm.achat(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"候选名单：\n{payload}\n\n请输出 JSON。"},
        ],
        temperature=0.0,
        max_tokens=4096,
    )
    print(f"LLM 清洗耗时: {time.time()-t0:.0f}s")

    import re
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    out = json.loads(m.group()) if m else {}
    json.dump(out, open(ROOT / "data" / "llm_clean_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    keep = [str(x) for x in out.get("keep") or []]
    keep_skills = [str(x) for x in out.get("keep_skills") or []]
    remove = [str(x.get("name")) for x in out.get("remove") or []]
    merge = out.get("merge") or []
    print(f"\nkeep: {len(keep)} | keep_skills: {len(keep_skills)} | remove: {len(remove)} | merge: {len(merge)}")
    print(f"\n删除: {remove}")
    print(f"\n合并: {json.dumps(merge, ensure_ascii=False)}")
    print(f"\n最终名单（keep + skills）: {len(keep)+len(keep_skills)} 个")
    for n in keep + keep_skills:
        print(f"  {n}")
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
