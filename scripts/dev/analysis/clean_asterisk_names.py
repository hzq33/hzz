"""清理存量数据中的星号角色名（井泽静江* → 井泽静江）。

NER 层已修（character_ner surface.rstrip(*※＊)），但存量数据
（inventory/roster/alias/dialogue_meta/story_analyses）仍带星号。
本脚本剥离所有 名字字段 的尾部星号标记。

用法:
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe \
        scripts/dev/analysis/clean_asterisk_names.py [--check]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_ASTERISK = "*※＊"


def _clean_name(n: str) -> str:
    return (n or "").rstrip(_ASTERISK).strip()


def clean_file(path: Path) -> tuple[object, int]:
    """清理单文件，返回 (修改后的数据, 替换处数)。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, 0
    changed = [0]

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                # per_character 结构：角色名作 key（value 是 {n, target, ...} 计数）
                if isinstance(k, str) and _clean_name(k) != k and isinstance(v, dict):
                    obj[_clean_name(k)] = v
                    del obj[k]
                    changed[0] += 1
                    continue
                if k in ("name", "canonical_name", "canonical", "speaker", "target") and isinstance(v, str):
                    c = _clean_name(v)
                    if c != v:
                        obj[k] = c
                        changed[0] += 1
                elif k == "source" and isinstance(v, str):
                    # story_analyses 的 relation source 也是角色名；alias 的 source 是 dict 会跳过
                    c = _clean_name(v)
                    if c != v:
                        obj[k] = c
                        changed[0] += 1
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and re.search(r"[\u4e00-\u9fff]+[*※＊]", item):
                    c = _clean_name(item)
                    if c != item:
                        obj[i] = c
                        changed[0] += 1
                elif isinstance(item, (dict, list)):
                    walk(item)

    walk(data)
    return data, changed[0]


def main(check_only: bool) -> int:
    targets = [
        ROOT / "data" / "inventories",
        ROOT / "data" / "rosters",
        ROOT / "data" / "dialogue_meta",
        ROOT / "data" / "story_analyses",
    ]
    total = 0
    for d in targets:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            data, n = clean_file(f)
            if n:
                total += n
                print(f"  {f.name}: {n} 处星号剥离")
                if not check_only and data is not None:
                    f.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
    print(f"[clean] {'--check: 共 ' if check_only else ''}{total} 处星号名待清理")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="清理存量星号角色名")
    ap.add_argument("--check", action="store_true", help="只扫描不写回")
    args = ap.parse_args()
    sys.exit(main(args.check))
