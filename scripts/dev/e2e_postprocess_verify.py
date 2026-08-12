"""LLM 后处理验证 — 用之前分析出问题的 10 次扮演测时间和效果。"""
import json
import os
import time

import requests

TOKEN = os.environ["AGENT_API_TOKEN"]
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}

# 之前分析出的问题样本（10 个，覆盖 P1 老赵错误 / P2 艾琳 OOC / 0 引用 / 时间关系）
CASES = [
    ("莉娜·沃伦", "你救过老赵吗？", "P1: 之前答'没有叫老赵的人'（原文有）"),
    ("艾琳·塔利斯", "你还会继续留在哨站吗？", "P2: 之前答'我只是商队首领'（原文她留下）"),
    ("莉娜·沃伦", "亚瑟和艾琳的关系，你怎么看？", "P3: 0 引用"),
    ("艾琳·塔利斯", "你母亲是怎么死的？", "P3: 0 引用"),
    ("艾琳·塔利斯", "你恨你父亲吗？", "P3: 0 引用"),
    ("玛拉·霍恩", "你父亲是个什么样的人？", "P3: 0 引用"),
    ("玛拉·霍恩", "你是什么时候加入黑旗军的？", "P3: 0 引用"),
    ("莉娜·沃伦", "你最喜欢采哪种草药？", "P3: 0 引用"),
    ("艾琳·塔利斯", "你潜伏在黑旗军里多久了？", "P3: 0 引用"),
    ("艾琳·塔利斯", "你最喜欢北境的什么？", "P3: 0 引用"),
]


def main() -> None:
    results = []
    for i, (ch, q, note) in enumerate(CASES, 1):
        t0 = time.time()
        try:
            r = requests.post(f"{BASE}/api/v1/agent/impersonate/chat", headers=H, json={
                "series_id": "北境守望者", "character": ch, "doc_id": "北境守望者__vol01",
                "message": q, "temperature": 0.85,
            }, timeout=180)
            dt = time.time() - t0
            d = r.json()
            row = {
                "idx": i, "character": ch, "question": q, "note": note,
                "reply": d.get("reply", ""), "n_citations": len(d.get("citations", [])),
                "latency_s": round(dt, 1), "http": r.status_code,
            }
        except Exception as exc:  # noqa: BLE001
            row = {
                "idx": i, "character": ch, "question": q, "note": note,
                "reply": "", "n_citations": 0, "latency_s": round(time.time() - t0, 1),
                "http": "ERR", "error": str(exc)[:100],
            }
        results.append(row)
        print(f"[{i}/10] {ch}「{q}」→ cit={row['n_citations']} ({row['latency_s']}s) http={row.get('http')}")
        if row.get("reply"):
            print(f"    {row['reply'][:100].replace(chr(10), ' ')}")
        time.sleep(0.5)

    # 汇总
    os.makedirs("data/eval", exist_ok=True)
    with open("data/eval/postprocess_verify.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    avg = sum(r["latency_s"] for r in results) / len(results)
    with_cit = sum(1 for r in results if r["n_citations"] > 0)
    print(f"\n===== 汇总 =====")
    print(f"平均耗时: {avg:.1f}s（修复前强制注入版 14s/单变体版 6s）")
    print(f"带引用: {with_cit}/10")


if __name__ == "__main__":
    main()
