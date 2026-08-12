"""attribution_coverage_check.py — 验证归属句是否在 expand 邻域内。

生产链路：child 命中 → expand ±1 → parent 邻域(2300字) → rerank → LLM。
本脚本回答：以「开头的 child（对话块），它的说话人归属句是否在
expand 后的邻域文本里？若在 → 分离无影响（LLM/rerank 看得到归属）；
若不在 → 分离有真实影响。

对每个「开头 child：
  1. 找其 parent（同章前序 parent）与 expand ±1 邻域
  2. 在邻域里搜索说话人线索：
     a. 邻近块文本里出现"XX说/道/问/答"（归属动词 + 前文说话人）
     b. 对话前文 3-8 字内出现人名（上下文推断式归属）
  3. 统计归属可恢复率

用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/attribution_coverage_check.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

ATTR_RE = re.compile(r"([\u4e00-\u9fff]{1,6})(说|道|问|答|喊|叫|低语|喃喃|叹|笑道?|说道)")


def main() -> None:
    import lancedb

    db = lancedb.connect(str(ROOT / "data" / "novel_lance"))
    t = db.open_table("novel_blocks")
    tab = t.to_lance().to_table(columns=[
        "global_id", "doc_id", "block_type", "chapter_title",
        "narrative_text", "style_tags_json",
    ])
    ids = tab.column("global_id").to_pylist()
    docs = tab.column("doc_id").to_pylist()
    bts = tab.column("block_type").to_pylist()
    nts = tab.column("narrative_text").to_pylist()
    tags = tab.column("style_tags_json").to_pylist()

    # 收集 child + parent 映射
    children: list[dict] = []
    parent_text: dict[str, str] = {}
    parent_order: dict[str, int] = {}
    parent_chapter: dict[str, str] = {}
    for gid, d, bt, nt, tj in zip(ids, docs, bts, nts, tags):
        try:
            p = json.loads(tj or "[]")
            hier = p.get("hierarchy", {}) if isinstance(p, dict) else {}
            gran = hier.get("granularity", "")
            pid = hier.get("parent_id", "")
        except Exception:
            continue
        if bt != "narrative":
            continue
        if gran == "child" and pid:
            children.append({"gid": gid, "pid": pid, "doc": d, "text": nt or "", "chapter": ""})
        elif gran == "parent":
            m = re.match(r"^.+_c(\d{3})_n(\d{4})$", gid)
            parent_text[gid] = nt or ""
            parent_order[gid] = int(m.group(2)) if m else -1
            parent_chapter[gid] = gid.split("_c")[0]

    # 按 doc+chapter 分组 parent，构建 ±1 邻域
    doc_ch_pars: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for gid, order in parent_order.items():
        ch = gid.split("_c")[0]
        doc_ch_pars[(gid.split("__")[0], ch)].append((order, gid))
    for v in doc_ch_pars.values():
        v.sort()

    def expand_text(pid: str) -> str:
        """expand ±1 邻域文本（同 doc+chapter 内）。"""
        m = re.match(r"^(.+)_c(\d{3})_n(\d{4})$", pid)
        if not m:
            return parent_text.get(pid, "")
        prefix, ch, n = m.group(1), m.group(2), int(m.group(3))
        doc = prefix.split("__")[0]
        pars = doc_ch_pars.get((doc, f"{prefix}_c{ch}"), [])
        # 找 pid 位置
        idx = next((i for i, (o, g) in enumerate(pars) if g == pid), -1)
        if idx < 0:
            return parent_text.get(pid, "")
        parts = []
        for j in range(max(0, idx - 1), min(len(pars), idx + 2)):
            parts.append(parent_text.get(pars[j][1], ""))
        return "\n".join(parts)

    # 只看「开头 child
    orphan_children = [c for c in children if c["text"].strip().startswith(("「", "『"))]
    print(f"child 总数 {len(children)}，以「开头 {len(orphan_children)}", flush=True)

    # 对每个孤儿 child：
    # 1. 自身文本里是否含归属（"XX说"在对话前后）
    # 2. parent 邻域（expand ±1）里是否含归属线索
    self_attr = 0
    neigh_attr = 0
    no_attr = 0
    samples_no = []
    for c in orphan_children:
        txt = c["text"]
        # 自身归属：对话块内或紧邻处出现"XX说"
        has_self = bool(ATTR_RE.search(txt))
        if has_self:
            self_attr += 1
            continue
        # 邻域归属：expand 文本里出现归属动词（且不是对话本身）
        exp = expand_text(c["pid"])
        # 去掉对话自身，看剩余文本
        rest = exp.replace(txt, "")
        if ATTR_RE.search(rest):
            neigh_attr += 1
        else:
            no_attr += 1
            if len(samples_no) < 5:
                samples_no.append((c["gid"], exp[:80].replace("\n", " ")))

    total = len(orphan_children)
    print(f"\n=== 归属可恢复性（expand 邻域内）===")
    print(f"  对话块自身含归属     : {self_attr}/{total} ({self_attr/total:.1%})")
    print(f"  邻域(±1 parent)含归属: {neigh_attr}/{total} ({neigh_attr/total:.1%})")
    print(f"  完全无归属线索       : {no_attr}/{total} ({no_attr/total:.1%})")
    print("\n  无归属样本（邻域前 80 字）:")
    for gid, exp in samples_no:
        print(f"    {gid}")
        print(f"      {exp!r}")

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "attribution_coverage.json"
    out.write_text(json.dumps({
        "total": total, "self_attr": self_attr,
        "neigh_attr": neigh_attr, "no_attr": no_attr,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    main()
