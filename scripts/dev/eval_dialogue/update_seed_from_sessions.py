"""更新 eval_seed.json — 从 imp sessions.db 新会话提取 case。

- 保留：败犬 case（有库数据）
- 删除：史莱姆观察日记 case（库中无对应数据）
- 新增：4 个新对话会话的 user query（温水和彦/袴田草介/温水佳树/雷姆）
"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SEED = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "eval_seed.json"
DB = ROOT / "data" / "sessions" / "imp" / "sessions.db"

# 新会话 → (角色, 作品, doc_prefix, aliases, speakers, gold)
# gold: 按 query 顺序的 gold_variants；无实体可标的 query 用 None（评 speaker_hit/semantic_overlap，
#       hit 类指标按设计不参与均分）。gold 为人工核对后的知识，新增会话必须填，缺失会触发末尾校验告警。
_GOLD_VARIANTS = {
    "温水和彦": ["温水和彦", "温水君", "温水"],
    "温水佳树": ["温水佳树", "佳树", "佳樹"],
    "八奈见杏菜": ["八奈见杏菜", "八奈", "八奈见"],
    "月之木古都": ["月之木古都", "会长"],
    "马剃天爱星": ["马剃天爱星", "马剃"],
    "袴田草介": ["袴田草介", "草芥", "草介"],
    "菜月•昴": ["菜月•昴", "菜月·昴", "菜月昴", "昴", "巴鲁斯"],  # 库里用 U+2022 bullet，需原样变体
}

NEW_SESSIONS = {
    "imp_e7d050c1c9fd": {
        "character": "温水和彦",
        "series": "败犬女主太多了",
        "doc_prefix": "败犬女主太多了",
        "doc_ids": ["败犬女主太多了__vol01"],
        "aliases": ["温水", "温温"],
        "speakers": ["温水和彦"],
        "intents": ["fact", "relationship", "relationship", "fact", "fact", "persona", "persona"],
        # 你妹妹是谁 / 你和你妹妹关系怎么样 / 你和八奈见杏菜怎么认识的 / 你知道学生会会长吗 /
        # 学生会副会长你应该认识吧（副会长=马剃天爱星，库原文佐证）/ 你平常都在做什么 / 你最喜欢看的书是什么
        "gold": [
            {"温水佳树": _GOLD_VARIANTS["温水佳树"]},
            {"温水佳树": _GOLD_VARIANTS["温水佳树"]},
            {"八奈见杏菜": _GOLD_VARIANTS["八奈见杏菜"]},
            {"月之木古都": _GOLD_VARIANTS["月之木古都"]},
            {"马剃天爱星": _GOLD_VARIANTS["马剃天爱星"]},
            None,
            None,
        ],
    },
    "imp_c572f9056d1f": {
        "character": "袴田草介",
        "series": "败犬女主太多了",
        "doc_prefix": "败犬女主太多了",
        "doc_ids": ["败犬女主太多了__vol01"],
        "aliases": ["草介", "袴田"],
        "speakers": ["袴田草介"],
        "intents": ["fact", "fact", "relationship", "relationship"],
        # 青梅竹马=八奈见杏菜；"你知道她喜欢你吗"的"她"=八奈见杏菜
        "gold": [
            {"八奈见杏菜": _GOLD_VARIANTS["八奈见杏菜"]},
            {"八奈见杏菜": _GOLD_VARIANTS["八奈见杏菜"]},
            {"八奈见杏菜": _GOLD_VARIANTS["八奈见杏菜"]},
            {"八奈见杏菜": _GOLD_VARIANTS["八奈见杏菜"]},
        ],
    },
    "imp_3123d2a9f2d5": {
        "character": "温水佳树",
        "series": "败犬女主太多了",
        "doc_prefix": "败犬女主太多了",
        "doc_ids": ["败犬女主太多了__vol01"],
        "aliases": ["佳树"],
        "speakers": ["温水佳树"],
        "intents": ["persona", "relationship", "fact"],
        # 请自我介绍一下 / 那你知道你哥哥喜欢谁吗 / 你认识你哥哥身边的女生吗
        "gold": [
            None,
            {"温水和彦": _GOLD_VARIANTS["温水和彦"]},
            {"温水和彦": _GOLD_VARIANTS["温水和彦"], "八奈见杏菜": _GOLD_VARIANTS["八奈见杏菜"]},
        ],
    },
    "imp_3e92fd88c139": {
        "character": "雷姆",
        "series": "Re：从零开始的异世界生活",
        "doc_prefix": "Re：从零开始的异世界生活",
        "doc_ids": ["Re：从零开始的异世界生活__vol34", "Re：从零开始的异世界生活__vol35"],
        "aliases": [],
        "speakers": ["雷姆"],
        "intents": ["persona", "persona", "persona", "relationship"],
        # 请自我介绍一下 / 你平常都在做什么 / 你喜欢的事物 / 你喜欢的人（雷姆喜欢昴）
        "gold": [
            None,
            None,
            None,
            {"菜月•昴": _GOLD_VARIANTS["菜月•昴"]},
        ],
    },
}


def extract_user_queries(session_id: str) -> list[str]:
    """从 sessions.db 提取该会话的 user 消息。"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT messages_json FROM sessions WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return []
    msgs = json.loads(row[0])
    return [
        str(m.get("content", "")).strip()
        for m in msgs
        if m.get("role") == "user" and str(m.get("content", "")).strip()
    ]


def load_character_info(series: str, character: str) -> dict:
    """从角色卡 JSON 提取 card_dialogues / catchphrases。"""
    # 尝试角色卡（data/characters/）
    for f in (ROOT / "data" / "characters").glob(f"*{character}*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return {
                "card_dialogues": [
                    {"speaker": d.get("speaker", character), "content": d.get("content", ""), "context": d.get("context", "")}
                    for d in (data.get("dialogues") or [])
                    if d.get("content")
                ],
                "catchphrases": [],
            }
        except Exception:
            continue
    return {}


def main() -> None:
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    old_cases = seed["cases"]
    old_chars = seed.get("characters", {})

    # 1. 保留败犬 case（有库数据），排除新会话的 case（幂等性：session_id 去重）
    kept = [
        c for c in old_cases
        if c.get("doc_prefix") == "败犬女主太多了"
        and c.get("session_id") not in NEW_SESSIONS
    ]
    print(f"保留败犬 case: {len(kept)}")
    # 去掉史莱姆角色
    kept_chars = {k: v for k, v in old_chars.items() if v.get("source_work") == "败犬女主太多了"}
    print(f"保留败犬角色: {list(kept_chars.keys())}")

    # 2. 从新会话提取 case
    new_cases = []
    new_char_meta = {}
    for sid, info in NEW_SESSIONS.items():
        queries = extract_user_queries(sid)
        char = info["character"]
        print(f"{sid} ({char}): {len(queries)} user queries")
        if len(queries) != len(info["intents"]):
            print(f"  ⚠️  intents 数量不匹配 ({len(queries)} vs {len(info['intents'])})，截断到 {min(len(queries), len(info['intents']))}")
        gold_list = info.get("gold") or [None] * len(info["intents"])
        if len(gold_list) != len(info["intents"]):
            print(f"  ⚠️  gold 数量不匹配 ({len(gold_list)} vs {len(info['intents'])})，缺失位补 None")
            gold_list = (gold_list + [None] * len(info["intents"]))[: len(info["intents"])]
        for i, q in enumerate(queries[: len(info["intents"])]):
            gv = gold_list[i] or {}
            new_cases.append({
                "id": f"{char}_{i+1:02d}",
                "query": q,
                "channel": "dialogue",
                "intent": info["intents"][i],
                "character": char,
                "aliases": info["aliases"],
                "doc_ids": info["doc_ids"],
                "doc_prefix": info["doc_prefix"],
                "gold_keywords": list(gv.keys()),
                "gold_speaker": char,
                "gold_variants": gv,
                "session_id": sid,
                "source": "real_session",
            })
        # characters 条目
        extra = load_character_info(info["series"], char)
        new_char_meta[char] = {
            "aliases": info["aliases"],
            "speakers": info["speakers"],
            "series_id": info["series"],
            "source_work": info["series"],
            "catchphrases": extra.get("catchphrases", []),
            "source_doc_ids": info["doc_ids"],
            "card_dialogues": extra.get("card_dialogues", []),
        }

    # 3. 合并
    seed["cases"] = kept + new_cases
    seed["characters"] = {**kept_chars, **new_char_meta}
    seed["version"] = 2
    seed["updated_at"] = "2026-08-07"
    seed["built_from"] = {
        **seed.get("built_from", {}),
        "imp_sessions": sorted(set(seed.get("built_from", {}).get("imp_sessions", [])) | set(NEW_SESSIONS.keys())),
    }

    SEED.write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写入 {SEED}")
    print(f"总 case: {len(seed['cases'])} (败犬 {len(kept)} + 新会话 {len(new_cases)})")
    print(f"角色: {list(seed['characters'].keys())}")

    # 校验
    from collections import Counter
    cc = Counter(c["character"] for c in seed["cases"])
    print(f"角色分布: {dict(cc)}")

    # gold 覆盖率校验（防止再次产出无 gold 半成品）
    no_gold = [c["id"] for c in seed["cases"] if not c.get("gold_variants")]
    tot = len(seed["cases"])
    print(f"gold 覆盖率: {tot - len(no_gold)}/{tot} = {(tot - len(no_gold)) / tot:.0%}")
    if no_gold:
        print(f"  ⚠️ 无 gold case {len(no_gold)} 条（无实体 query 属正常，有实体未标属缺陷）: {no_gold}")


if __name__ == "__main__":
    main()
