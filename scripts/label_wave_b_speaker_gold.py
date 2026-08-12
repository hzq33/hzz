"""One-shot Wave B gold labeling helper."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TENSURA = {
    "g001": "利姆路",
    "g002": "利格鲁",
    "g003": "利姆路",
    "g004": "利格鲁",
    "g005": "利姆路",
    "g006": "利格鲁",
    "g007": "利姆路",
    "g008": "利格鲁",
    "g009": "利姆路",
    "g010": "利格鲁",
    "g011": "利姆路",
    "g012": "利格鲁",
    "g013": "利姆路",
    "g014": "兰加",
    "g015": "利姆路",
    "g016": "兰加",
    "g017": "利姆路",
    "g018": "兰加",
    "g019": "利姆路",
    "g020": "兰加",
    "g021": "利格鲁",
    "g022": "哥布达",
    "g023": "利姆路",
    "g024": "哥布达",
    "g025": "利姆路",
    "g026": "哥布达",
    "g027": "利格鲁",
    "g028": "利格鲁",
    "g029": "旁观人类",
    "g030": "旁观人类",
    "g031": "旁观人类",
    "g032": "旁观人类",
    "g033": "盗贼",
    "g034": "盗贼",
    "g035": "利姆路",
    "g036": "哥布达",
    "g037": "利姆路",
    "g038": "哥布达",
    "g039": "利姆路",
    "g040": "哥布达",
    "g041": "盗贼",
    "g042": "盗贼",
    "g043": "利姆路",
    "g044": "利姆路",
    "g045": "哥布达",
    "g046": "利姆路",
    "g047": "盗贼",
    "g048": "盗贼",
    "g049": "利姆路",
    "g050": "盗贼",
    "g051": "盗贼",
    "g052": "利姆路",
    "g053": "盗贼",
    "g054": "盗贼",
    "g055": "利姆路",
    "g056": "盗贼",
    "g057": "盗贼",
    "g058": "利姆路",
}


def label_tensura() -> None:
    p = ROOT / "tests/eval/speaker_gold/tensura_ch1_draft/gold.jsonl"
    rows = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sp = TENSURA.get(row["id"], "")
        row["speaker"] = sp
        row["note"] = "labeled-wave-b" if sp else row.get("note", "")
        rows.append(row)
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    labeled = sum(1 for r in rows if r["speaker"])
    print(f"tensura labeled {labeled}/{len(rows)}")


def expand_classroom() -> None:
    excerpt = ROOT / "tests/eval/speaker_gold/classroom_a/chapter_excerpt.md"
    more = """
温水点了点头，压低声音。
「那就说好了。明天下午见。」
姬宫笑得更开心了。
「太好了！我去挑电影票。」
八奈见轻轻哼了一声，却没有反对。
「随便你们。不过别选恐怖片。」
温水看着两人，终于也笑了。
「知道了。我们走吧。」
走到校门口时，姬宫突然回头。
「对了，温水同学——作业记得带齐。」
温水一愣，随即苦笑。
「你才是别忘了课本。」
八奈见挥了挥手。
「明天见。」
温水应了一声。
「嗯，明天见。」
姬宫已经跑出几步，回头喊道。
「我先走啦，掰掰～」
八奈见皱眉叫住她。
「等等，钥匙还在你那儿。」
姬宫回身，把钥匙塞回来。
「啊，对不起。给你。」
"""
    text = excerpt.read_text(encoding="utf-8").rstrip() + "\n" + more.strip() + "\n"
    excerpt.write_text(text, encoding="utf-8")

    extra_gold = [
        ("g010", "那就说好了。明天下午见。", "温水"),
        ("g011", "太好了！我去挑电影票。", "姬宫"),
        ("g012", "随便你们。不过别选恐怖片。", "八奈见"),
        ("g013", "知道了。我们走吧。", "温水"),
        ("g014", "对了，温水同学——作业记得带齐。", "姬宫"),
        ("g015", "你才是别忘了课本。", "温水"),
        ("g016", "明天见。", "八奈见"),
        ("g017", "嗯，明天见。", "温水"),
        ("g018", "我先走啦，掰掰～", "姬宫"),
        ("g019", "等等，钥匙还在你那儿。", "八奈见"),
        ("g020", "啊，对不起。给你。", "姬宫"),
    ]
    gp = ROOT / "tests/eval/speaker_gold/classroom_a/gold.jsonl"
    existing = [
        json.loads(l) for l in gp.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    have = {r["id"] for r in existing}
    for gid, content, speaker in extra_gold:
        if gid in have:
            continue
        existing.append(
            {
                "id": gid,
                "content": content,
                "speaker": speaker,
                "chapter": "demo",
                "note": "wave-b",
            }
        )
    gp.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in existing) + "\n",
        encoding="utf-8",
    )
    labeled = sum(1 for r in existing if r.get("speaker"))
    print(f"classroom labeled {labeled}/{len(existing)}")


if __name__ == "__main__":
    label_tensura()
    expand_classroom()
