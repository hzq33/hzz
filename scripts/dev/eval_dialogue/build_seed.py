"""build_seed.py — 构建对话检索评估集（L1）。

数据来源（全部真实）：
  1. data/sessions/imp/sessions.db   — 口吻模仿真实会话（角色 + 用户 query）
  2. data/sessions/sessions.db       — 通用 chat 会话
  3. data/characters/*.json          — 角色卡（aliases + dialogues 金标台词）
  4. data/rosters/*.alias.json       — alias 归并结果（别名映射）
  5. data/inventories/*.json         — seed_names 实体词典（gold_keywords 提取）

输出：scripts/dev/eval_dialogue/data/eval_seed.json

用法：
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/build_seed.py
"""

from __future__ import annotations

import glob
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # scripts/dev/eval_dialogue/.. → 项目根
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "scripts" / "dev" / "eval_dialogue" / "data"
OUT_PATH = OUT_DIR / "eval_seed.json"

IMP_DB = ROOT / "data" / "sessions" / "imp" / "sessions.db"
CHAT_DB = ROOT / "data" / "sessions" / "sessions.db"
CHAR_DIR = ROOT / "data" / "characters"
ROSTER_DIR = ROOT / "data" / "rosters"
INV_DIR = ROOT / "data" / "inventories"

# 寒暄/无信息 query：保留为 chitchat case 但单列统计；每条会话最多保留 1 条
_CHITCHAT = {"你好", "你好啊", "你好呀", "哈喽", "嗨", "在吗", "嗯", "哈哈", "我们聊些什么", "你爱自己的国家吗"}
# 系统功能性 query（非小说检索评估对象）→ 排除
_EXCLUDE = {"有哪些小说"}
# 用户口语译名变体 → 规范实体名（人工核对后冻结；query 匹配与 gold 检索均使用规范名）
_MANUAL_ALIASES = {
    "维鲁多拉": "维尔德拉",
    "伊芙利特": "伊夫利特",
    "伊芙利德": "伊夫利特",
    "库洛艾": "克萝耶",
    "克罗艾": "克萝耶",
    "克萝耶": "克萝耶",
    "日向": "坂口日向",
    "坂口日向": "坂口日向",
    "温水君": "温水和彦",
    "温水": "温水和彦",
    "草芥": "袴田草介",
    "草介": "袴田草介",
    "会长": "月之木古都",
}


def _norm(s: str) -> str:
    """归一化：去空白与日文/中文分隔符号，用于宽松匹配。"""
    return "".join(s.split()).replace("・", "").replace("·", "").replace("、", "").replace("，", "").replace(",", "").replace("—", "")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_sessions(db_path: Path) -> list[dict]:
    """读会话库，返回 [{session_id, character, queries: [str]}]。"""
    out: list[dict] = []
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute("SELECT session_id, metadata_json FROM sessions")
    for sid, meta in cur.fetchall():
        m = json.loads(meta) if meta else {}
        char = m.get("character") or ""
        cur2 = con.cursor()
        cur2.execute("SELECT messages_json FROM sessions WHERE session_id=?", (sid,))
        raw = cur2.fetchone()[0]
        msgs = json.loads(raw)
        queries = [
            str(x.get("content", "")).strip()
            for x in msgs
            if x.get("role") == "user" and str(x.get("content", "")).strip()
        ]
        out.append({"session_id": sid, "character": char, "queries": queries})
    con.close()
    return out


def load_characters() -> dict[str, dict]:
    """读角色卡 → {canonical_name: {aliases, dialogues, series_id, source_work}}。"""
    chars: dict[str, dict] = {}
    for f in glob.glob(str(CHAR_DIR / "*.json")):
        if "alias" in f:
            continue
        d = _load_json(Path(f))
        p = d.get("profile") or {}
        name = (p.get("name") or "").strip()
        if not name:
            continue
        aliases = [a for a in (p.get("aliases") or []) if isinstance(a, str) and a.strip()]
        dialogues = [
            {"speaker": x.get("speaker", ""), "content": x.get("content", ""), "context": x.get("context", "")}
            for x in (d.get("dialogues") or [])
            if isinstance(x, dict) and x.get("content")
        ]
        catchphrases = [
            str(x) for x in ((p.get("structured_catchphrases") or p.get("catchphrases")) or [])
            if isinstance(x, str) and x.strip()
        ]
        chars[name] = {
            "aliases": list(dict.fromkeys([name] + aliases)),
            "dialogues": dialogues,
            "catchphrases": catchphrases,
            "series_id": p.get("series_id", ""),
            "source_work": p.get("source_work", ""),
            "source_doc_ids": p.get("source_doc_ids") or [],
        }
    return chars


def load_alias_map() -> dict[str, list[str]]:
    """读 roster alias.json → {canonical_name: [aliases]}。"""
    alias_map: dict[str, list[str]] = {}
    for f in glob.glob(str(ROSTER_DIR / "*.alias.json")):
        d = _load_json(Path(f))
        for ent in d.get("entities") or []:
            name = (ent.get("canonical_name") or "").strip()
            if not name:
                continue
            aliases = [a for a in (ent.get("aliases") or []) if isinstance(a, str) and a.strip()]
            alias_map[name] = list(dict.fromkeys([name] + aliases))
    return alias_map


def load_entity_dict() -> list[str]:
    """所有 inventory 的 seed_names 合并 → 实体词典（gold_keywords 提取用）。"""
    names: list[str] = []
    for f in glob.glob(str(INV_DIR / "*.json")):
        d = _load_json(Path(f))
        names.extend(str(n) for n in (d.get("seed_names") or []) if isinstance(n, str))
    # 去重 + 按长度降序（长实体优先匹配）
    return sorted(set(names), key=len, reverse=True)


def classify_intent(query: str) -> str:
    q = query.strip()
    if q in _CHITCHAT or len(q) <= 4 or q.startswith("你好"):
        return "chitchat"
    if "关系" in q or "喜欢" in q or "看法" in q or "怎么看" in q or "情敌" in q:
        return "relationship"
    if "发生了什么" in q or "发展" in q or "后来" in q or "第二卷" in q or "第三卷" in q:
        return "plot"
    if "为什么" in q or "怎么" in q or "什么" in q or "吗" in q or "呢" in q or "谁" in q:
        return "fact"
    return "fact"


def extract_keywords(query: str, alias_dict: dict[str, str]) -> list[str]:
    """query 中命中的实体（alias 映射后）作为 gold_keywords。

    alias_dict: {规范名(去符号): [该名全部变体]}，匹配用归一化子串。
    """
    hits: list[str] = []
    q_norm = _norm(query)
    for canon, variants in alias_dict.items():
        if any(_norm(v) and _norm(v) in q_norm for v in variants):
            hits.append(canon)
    return hits


def main() -> None:
    imp_sessions = read_sessions(IMP_DB)
    chat_sessions = read_sessions(CHAT_DB)
    chars = load_characters()
    alias_map = load_alias_map()
    entity_dict = load_entity_dict()

    # 合并 alias：角色卡 aliases 优先，alias.json 兜底
    for name, info in chars.items():
        extra = alias_map.get(name, [])
        info["aliases"] = list(dict.fromkeys(info["aliases"] + extra))

    # 构建别名词典：{canonical: [全部变体]}，含 inventory seed_names 与人工变体表
    alias_dict: dict[str, list[str]] = {}

    def add_variant(canon: str, v: str) -> None:
        alias_dict.setdefault(canon, [])
        if v not in alias_dict[canon]:
            alias_dict[canon].append(v)

    for name, info in chars.items():
        for a in info["aliases"]:
            add_variant(name, a)
    for n in entity_dict:
        add_variant(n, n)
    for variant, canon in _MANUAL_ALIASES.items():
        add_variant(canon, variant)

    cases: list[dict] = []
    seen: set[tuple[str, str, str]] = set()  # (channel, character, query)
    chitchat_kept: set[str] = set()          # 每角色寒暄只留 1 条

    def add_case(channel: str, char: str, query: str, intent: str, session_id: str) -> None:
        nonlocal seen
        key = (channel, char, query)
        if key in seen:
            return
        seen.add(key)
        kws = extract_keywords(query, alias_dict)
        # gold_variants：每个命中规范名的全部变体（hit 判定用变体集，覆盖库内用字差异）
        variants: dict[str, list[str]] = {}
        for kw in kws:
            vlist = [v for v in alias_dict.get(kw, []) if v]
            variants[kw] = list(dict.fromkeys([kw] + vlist))
        info = chars.get(char, {})
        doc_ids = info.get("source_doc_ids") or []
        doc_prefix = ""
        if doc_ids:
            m = re.match(r"^(.*?)__vol\d+", doc_ids[0])
            if m:
                doc_prefix = m.group(1)
        cases.append(
            {
                "id": f"{char}_{len(cases) + 1:02d}" if channel == "dialogue" and char else (
                    f"{char}_n{len(cases) + 1:02d}" if char else f"chat_n{len(cases) + 1:02d}"
                ),
                "query": query,
                "character": char,
                "channel": channel,
                "intent": intent,
                "source": "real_session",
                "session_id": session_id,
                "gold_speaker": char or None,
                "aliases": info.get("aliases", []),
                "gold_keywords": kws,
                "gold_variants": variants,
                "doc_prefix": doc_prefix,
                "doc_ids": doc_ids,
            }
        )

    for sess in imp_sessions:
        char = sess["character"]
        for q in sess["queries"]:
            if q in _EXCLUDE:
                continue
            intent = classify_intent(q)
            if intent == "chitchat":
                if char in chitchat_kept:
                    continue
                chitchat_kept.add(char)
                add_case("dialogue", char, q, intent, sess["session_id"])
            else:
                add_case("dialogue", char, q, intent, sess["session_id"])
                # 双通道：情节/事实/关系类 query 同时评 narrative 通道
                add_case("narrative", char, q, intent, sess["session_id"])

    for sess in chat_sessions:
        for q in sess["queries"]:
            if q in _EXCLUDE:
                continue
            if q in _CHITCHAT or len(q) <= 4 or q.startswith("你好"):
                continue
            add_case("narrative", "", q, classify_intent(q), sess["session_id"])

    # 角色信息节（gold 台词池 = 角色卡 dialogues + speaker 集合）
    characters_section = {}
    for name, info in chars.items():
        if any(c["character"] == name for c in cases):
            # speaker 集合：角色卡 dialogues 的 speaker + aliases + 人工变体（归一化去符号）
            speakers = {_norm(s) for s in info["aliases"]}
            for dlg in info["dialogues"]:
                sp = (dlg.get("speaker") or "").strip()
                if sp:
                    speakers.add(_norm(sp))
            for variant, canon in _MANUAL_ALIASES.items():
                if canon == name:
                    speakers.add(_norm(variant))
            characters_section[name] = {
                "aliases": info["aliases"],
                "speakers": sorted(s for s in speakers if s),
                "series_id": info["series_id"],
                "source_work": info["source_work"],
                "catchphrases": info.get("catchphrases", []),
                "source_doc_ids": info.get("source_doc_ids") or [],
                "card_dialogues": info["dialogues"],
            }

    seed = {
        "version": 1,
        "created_at": None,  # 由 run_eval 报告填充时间戳
        "built_from": {
            "imp_sessions": [s["session_id"] for s in imp_sessions],
            "chat_sessions": [s["session_id"] for s in chat_sessions],
        },
        "characters": characters_section,
        "cases": cases,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")

    # 统计输出
    by_char = Counter(c["character"] or "chat" for c in cases)
    by_intent = Counter(c["intent"] for c in cases)
    by_channel = Counter(c["channel"] for c in cases)
    no_kw = [c["id"] for c in cases if not c["gold_keywords"]]
    print(f"cases: {len(cases)}")
    print(f"by_char: {dict(by_char)}")
    print(f"by_intent: {dict(by_intent)}")
    print(f"by_channel: {dict(by_channel)}")
    print(f"characters with gold: {len(characters_section)}")
    print(f"cases without gold_keywords: {no_kw}")
    print(f"wrote: {OUT_PATH}")


if __name__ == "__main__":
    main()
