"""100 次扮演评估 — 多角色 × 四方向提问，收集指标。

方向：
- daily   日常交流类（角色口吻/性格/生活）
- relation 角色关系类（谁和谁什么关系/看法）
- temporal 时间类（后来/之前/五年前/什么时候）
- event   事件发展类（发生了什么/结果如何）

角色：8 个已建卡角色（亚瑟/莉娜/维克托/艾琳/雷恩/小卡/玛拉/老首领）
每角色 12-13 问，总计 100 问。

输出：
- data/eval/impersonation_100_results.jsonl  每次回答+指标
- 汇总统计打印
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

TOKEN = os.environ["AGENT_API_TOKEN"]
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}
SERIES = "北境守望者"

# ── 问题集：{角色: [(方向, 问题)]} ────────────────────────────
QUESTIONS: dict[str, list[tuple[str, str]]] = {
    "亚瑟·卡恩": [
        ("daily", "你最喜欢哨站的哪道菜？"),
        ("daily", "你平时巡逻的时候都在想什么？"),
        ("daily", "你觉得雷恩这个孩子怎么样？"),
        ("relation", "莉娜在你心里是什么位置？"),
        ("relation", "你恨维克托叔叔吗？"),
        ("relation", "艾琳说她是你的姐姐，你信吗？"),
        ("relation", "你父亲是个什么样的人？"),
        ("temporal", "十年前你父亲战死的时候，你在哪里？"),
        ("temporal", "你是什么时候当上指挥官的？"),
        ("temporal", "后来黑旗军怎么样了？"),
        ("event", "你和莉娜是怎么在一起的？"),
        ("event", "黑鸦堡那一趟，到底发生了什么？"),
    ],
    "莉娜·沃伦": [
        ("daily", "你平时都在医帐里做什么？"),
        ("daily", "雪莲真的能治寒症吗？"),
        ("daily", "柱子跟着你学医，你觉得他怎么样？"),
        ("relation", "你和亚瑟是怎么认识的？"),
        ("relation", "亚瑟和艾琳的关系，你怎么看？"),
        ("relation", "维克托副官对哨站重要吗？"),
        ("temporal", "五年前那次流寇围攻，到底发生了什么？"),
        ("temporal", "你什么时候开始喜欢亚瑟的？"),
        ("event", "那次瘟疫，你是怎么熬过来的？"),
        ("event", "亚瑟去黑鸦堡的时候，你担心吗？"),
        ("daily", "你最喜欢采哪种草药？"),
        ("event", "你救过老赵吗？"),
    ],
    "维克托·黑森": [
        ("daily", "你每天擦那把剑，是因为什么？"),
        ("daily", "柱子跟你学射箭，你觉得他有天赋吗？"),
        ("relation", "马库斯是个什么样的人？"),
        ("relation", "你后悔当年打开山口吗？"),
        ("relation", "亚瑟现在当指挥官，你满意吗？"),
        ("temporal", "三十年前你是怎么来到灰脊哨站的？"),
        ("temporal", "十年前山口那一仗，到底是怎么回事？"),
        ("event", "你为什么要背叛马库斯？"),
        ("event", "黑旗军来袭那一战，你守的是哪里？"),
        ("daily", "你教过多少人射箭？"),
        ("relation", "铁山这个人，你觉得可靠吗？"),
        ("temporal", "你现在还放不下马库斯的事吗？"),
    ],
    "艾琳·塔利斯": [
        ("daily", "你在南方这十年，是怎么过的？"),
        ("daily", "你还会回南方吗？"),
        ("relation", "你恨你父亲吗？"),
        ("relation", "亚瑟这个弟弟，你觉得怎么样？"),
        ("relation", "老首领是个什么样的人？"),
        ("temporal", "你是什么时候知道自己是马库斯女儿的？"),
        ("temporal", "你潜伏在黑旗军里多久了？"),
        ("event", "黑鸦堡那一晚，你见到老首领的时候在想什么？"),
        ("event", "你母亲是怎么死的？"),
        ("daily", "你最喜欢北境的什么？"),
        ("relation", "玛拉和你是什么关系？"),
        ("event", "你还会继续留在哨站吗？"),
    ],
    "雷恩·索恩": [
        ("daily", "你巡逻的时候喜欢唱歌，是真的吗？"),
        ("daily", "你最怕小卡姐什么？"),
        ("relation", "亚瑟指挥官对你来说是什么？"),
        ("relation", "你和阿贵是怎么认识的？"),
        ("relation", "铁山哥投降以后，你信任他吗？"),
        ("temporal", "你是什么时候来哨站的？"),
        ("temporal", "你以前在南方流浪的时候，是什么样子的？"),
        ("event", "你第一次带队巡逻，是什么时候？"),
        ("event", "你抓到过黑旗军的甲片吗？"),
        ("daily", "你最大的愿望是什么？"),
        ("event", "那次发现黑旗军余党的经过，讲讲？"),
        ("relation", "柱子喊你哥哥，你高兴吗？"),
    ],
    "卡洛琳·怀特": [
        ("daily", "你的鹿肉汤为什么这么好喝？"),
        ("daily", "你最拿手的菜是什么？"),
        ("relation", "柱子这孩子，你当他是弟弟吗？"),
        ("relation", "你恨过把你爹娘带走的那场瘟疫吗？"),
        ("temporal", "你是什么时候来哨站的？"),
        ("temporal", "你以前的日子，是怎么过的？"),
        ("event", "你救过那个冻晕在墙根的少年吗？"),
        ("event", "黑旗军来袭那天，你在做什么？"),
        ("daily", "你为什么要学写字？"),
        ("relation", "马库斯指挥官当年是怎么收留你的？"),
        ("event", "你种过雪莲吗？"),
        ("daily", "你最大的心愿是什么？"),
    ],
    "玛拉·霍恩": [
        ("daily", "霍恩商行现在做什么生意？"),
        ("daily", "你为什么要写书？"),
        ("relation", "你父亲是个什么样的人？"),
        ("relation", "铁山杀了你父亲，你恨他吗？"),
        ("relation", "周诚是个什么样的人？"),
        ("temporal", "你是什么时候加入黑旗军的？"),
        ("temporal", "你潜伏在黑旗军里多少年了？"),
        ("event", "你为什么要投降北境？"),
        ("event", "永冻之心的传说，是真的吗？"),
        ("daily", "你最喜欢北境的什么？"),
        ("event", "霍恩商行分号是怎么开起来的？"),
        ("relation", "你和艾琳是什么关系？"),
    ],
    "老首领": [
        ("daily", "你年轻的时候，是个什么样的人？"),
        ("daily", "你现在还有什么放不下的？"),
        ("relation", "马库斯·卡恩，你恨他吗？"),
        ("relation", "艾琳这个养女，你对她有感情吗？"),
        ("relation", "维克托当年打开山口，你信他吗？"),
        ("temporal", "你是什么时候开始找永冻之心的？"),
        ("temporal", "你儿子小虎，是什么时候没的？"),
        ("event", "十年前山口那一仗，到底是怎么回事？"),
        ("event", "你为什么要杀艾琳的母亲？"),
        ("daily", "你还记得北境的雪吗？"),
        ("event", "你最后是怎么死的？"),
        ("event", "铁山为什么会背叛你？"),
    ],
}

# 关系类问题数量统计（用于分析）
REL_QA = sum(1 for qs in QUESTIONS.values() for d, _ in qs if d == "relation")
TIME_QA = sum(1 for qs in QUESTIONS.values() for d, _ in qs if d == "temporal")
EVENT_QA = sum(1 for qs in QUESTIONS.values() for d, _ in qs if d == "event")
DAILY_QA = sum(1 for qs in QUESTIONS.values() for d, _ in qs if d == "daily")


def impersonate(character: str, message: str, session_id: str, timeout: int = 180) -> dict:
    """调用扮演接口，返回回复+引用。doc_id 锁定系列（否则角色卡系列推断错误）。"""
    payload = {
        "series_id": SERIES,
        "character": character,
        "message": message,
        "session_id": session_id,
        "doc_id": "北境守望者__vol01",
        "temperature": 0.85,
    }
    r = requests.post(
        f"{BASE}/api/v1/agent/impersonate/chat", headers=H, json=payload, timeout=timeout
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "body": r.text[:300]}
    d = r.json()
    return {
        "reply": d.get("reply", ""),
        "citations": d.get("citations", []),
        "session_id": d.get("session_id", session_id),
    }


def main() -> None:
    os.makedirs("data/eval", exist_ok=True)
    out_path = "data/eval/impersonation_100_results.jsonl"
    results = []
    idx = 0
    total = sum(len(qs) for qs in QUESTIONS.values())
    print(f"总问题数: {total}（daily={DAILY_QA} relation={REL_QA} temporal={TIME_QA} event={EVENT_QA}）")
    with open(out_path, "w", encoding="utf-8") as f:
        for character, qs in QUESTIONS.items():
            for direction, q in qs:
                idx += 1
                sid = f"eval_{character[:2]}_{idx}"
                try:
                    t0 = time.time()
                    res = impersonate(character, q, sid)
                    dt = time.time() - t0
                    row = {
                        "idx": idx,
                        "character": character,
                        "direction": direction,
                        "question": q,
                        "reply": res.get("reply", ""),
                        "citations": res.get("citations", []),
                        "n_citations": len(res.get("citations", [])),
                        "session_id": sid,
                        "latency_s": round(dt, 1),
                        "error": res.get("error"),
                    }
                except Exception as exc:  # noqa: BLE001
                    row = {
                        "idx": idx, "character": character, "direction": direction,
                        "question": q, "reply": "", "citations": [],
                        "n_citations": 0, "session_id": sid, "latency_s": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append(row)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                status = "ERR" if row.get("error") else f"cit={row['n_citations']}"
                print(f"[{idx}/{total}] {character} [{direction}] {q[:22]}… → {status} ({row['latency_s']}s)", flush=True)
                time.sleep(0.5)
    # 汇总
    errs = [r for r in results if r.get("error")]
    with_cit = [r for r in results if r.get("n_citations", 0) > 0]
    print("\n===== 汇总 =====")
    print(f"总数: {len(results)}  成功: {len(results)-len(errs)}  失败: {len(errs)}")
    print(f"带引用: {len(with_cit)} ({len(with_cit)/max(len(results),1)*100:.1f}%)")
    if errs:
        print("错误样例:", [e["error"][:80] for e in errs[:3]])
    # 每方向带引用率
    for d_ in ("daily", "relation", "temporal", "event"):
        sub = [r for r in results if r["direction"] == d_]
        cit = [r for r in sub if r.get("n_citations", 0) > 0]
        print(f"  {d_}: {len(sub)} 问, 带引用 {len(cit)} ({len(cit)/max(len(sub),1)*100:.0f}%)")


if __name__ == "__main__":
    main()
