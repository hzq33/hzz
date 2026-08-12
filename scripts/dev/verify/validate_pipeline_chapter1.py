# -*- coding: utf-8 -*-
"""一章验证：glm 粗扫（候选名+关系）→ DeepSeek 带 evidence 精识别"""
import asyncio, json, re, sys
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", encoding="utf-8-sig")

GLM_KEY = "a2499d3bcf7a4b58b1ea3fe101403d0f.tLFegfWTXnuu8cQ7"
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


async def glm_extract(text: str) -> dict:
    """glm 联合抽取（说话人+关系），模拟阶段1。"""
    import json as j, urllib.request

    from src.domain.novel.character_inventory.llm_ner import SYSTEM_PROMPT
    body = j.dumps({
        "model": "glm-4.7-flash",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text + "\n\n请输出 JSON。"},
        ],
        "temperature": 0, "max_tokens": 4096,
        "thinking": {"type": "disabled"},
    }).encode()
    req = urllib.request.Request(GLM_URL, data=body, headers={
        "Authorization": f"Bearer {GLM_KEY}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = j.loads(r.read())["choices"][0]["message"]["content"]
            return raw
        except Exception as e:
            if attempt < 3:
                await asyncio.sleep(6)
            else:
                raise
    return ""


def parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group()) if m else {}


def take_evidence(text: str, name: str, n: int = 3, win: int = 60) -> list[str]:
    """原文定位取 evidence 片段。"""
    out = []
    start = 0
    while len(out) < n:
        i = text.find(name, start)
        if i < 0:
            break
        a = max(0, i - win)
        b = min(len(text), i + len(name) + win)
        snip = text[a:b].replace("\n", " ").strip()
        if snip and snip not in out:
            out.append(snip)
        start = i + len(name)
    return out


async def main():
    from src.application.novel.redialogue import rebuild_chapters

    chapters = rebuild_chapters("关于我转生变成史莱姆这档事__vol01")
    ch = chapters[9]  # 外传 哥布达大冒险
    text = ch.text
    print(f"章节: {ch.title} ({len(text)} 字符)\n")

    # ── 阶段1：glm 粗扫 ──
    print("【阶段1】glm 联合抽取...")
    raw = await glm_extract(text)
    data = parse(raw)
    names = [str(n) for n in data.get("names") or []]
    rels = data.get("relations") or []
    print(f"glm 候选名: {len(names)} | 关系: {len(rels)}")
    print(f"  候选名: {names}")
    for r in rels[:10]:
        print(f"  关系: {r.get('source')} → {r.get('target')} [{r.get('relation')}] ev={str(r.get('evidence'))[:40]}")

    # ── 阶段2：DeepSeek 带 evidence 精识别 ──
    print("\n【阶段2】DeepSeek 带 evidence 归一裁决...")
    from src.shared.llm import SharedLLMClient
    from src.utils.config import load_config
    from src.shared.llm_config import get_endpoint_config

    cfg = load_config(str(ROOT / "config.yaml"))
    ep = get_endpoint_config("character_inventory_normalize")
    llm = SharedLLMClient(
        primary={
            "model": ep.get("model") or "deepseek-v4-pro",
            "api_key": ep.get("api_key") or cfg.get("agent", {}).get("api_key", ""),
            "base_url": ep.get("base_url") or "https://api.deepseek.com",
        },
        temperature=0.0, max_tokens=4096,
    )

    cand_lines = []
    for n in names:
        evs = take_evidence(text, n)
        cand_lines.append(f"- {n} (evidence: {' | '.join(evs[:2])})")

    SYSTEM = """你是小说角色名单清洗器。给定候选角色名（含原文 evidence），对每个裁决：
- keep：真实角色（有名字的人物）
- keep_skills：会说话的技能/系统音（如史莱姆的「大贤者」——有台词）
- remove：泛称（商人/勇者/国家）、种族（矮人/人类）、地名、普通物品、作者/注释人物
- merge：同一角色重复条目（凯金+凯多）
规则：以 evidence 为准；拿不准 → keep。
输出 JSON：{"keep":[...],"keep_skills":[...],"remove":[{"name":"...","reason":"..."}],"merge":[{"canonical":"...","names":[...]}]}"""

    t0 = __import__("time").time()
    raw2 = await llm.achat(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": "候选名单：\n" + "\n".join(cand_lines) + "\n\n请输出 JSON。"}],
        temperature=0.0, max_tokens=4096,
    )
    print(f"DeepSeek 裁决耗时: {__import__('time').time()-t0:.0f}s")
    m = re.search(r"\{.*\}", raw2, re.DOTALL)
    out = json.loads(m.group()) if m else {}
    json.dump(out, open(ROOT / "data" / "chapter1_clean_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    keep = [str(x) for x in out.get("keep") or []]
    skills = [str(x) for x in out.get("keep_skills") or []]
    remove = out.get("remove") or []
    merge = out.get("merge") or []
    print(f"\n保留角色: {len(keep)} {keep}")
    print(f"保留技能: {len(skills)} {skills}")
    print(f"删除: {[r.get('name') for r in remove]}")
    print(f"合并: {json.dumps(merge, ensure_ascii=False)}")
    print(f"\n最终名单: {len(keep)+len(skills)} 个 → {keep + skills}")
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
