"""E2E 验证：剧情分析 + 时间线 + 设定书 + GraphRAG（H 域）。"""
import json
import os
import sys

import requests

TOKEN = os.environ["AGENT_API_TOKEN"]
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}
SID = "e2e_verify_series"


def show(label, r):
    print(f"\n=== {label} → HTTP {r.status_code}")
    try:
        d = r.json()
        if isinstance(d, dict) and d.get("exists") is False:
            print("  exists=false（尚无数据，符合预期）")
            return d
        # 摘要
        if label == "剧情分析":
            print(f"  events={len(d.get('events') or [])} relations={len(d.get('relations') or [])} foreshadows={len(d.get('foreshadows') or [])}")
            for ev in (d.get("events") or [])[:3]:
                print(f"    · {ev.get('doc_id','')} [{ev.get('chapter_no','')}] {str(ev.get('summary') or ev.get('event_type') or '')[:50]}")
        elif label == "时间线":
            print(f"  chronicle={len(d.get('chronicle') or [])} by_character={len(d.get('by_character') or {})}")
        elif label == "设定书":
            print(f"  entries={len(d.get('entries') or [])}")
        elif label == "GraphRAG":
            print(f"  exists={d.get('exists')} stale={d.get('stale')} communities={len(d.get('communities') or [])} overview={str(d.get('global_overview') or '')[:60]}")
        else:
            print(f"  keys={list(d.keys())[:10]}")
        return d
    except Exception as e:
        print(f"  parse err: {e} | {r.text[:200]}")
        return None


# 1. 现有剧情分析（应该还没有）
r = requests.get(f"{BASE}/api/v1/agent/story-analysis", headers=H, params={"series_id": SID}, timeout=15)
show("剧情分析(初始)", r)

# 2. 构建剧情分析（同步）
r = requests.post(f"{BASE}/api/v1/agent/story-analysis/build", headers=H,
                  json={"series_id": SID, "force": True, "wait": True}, timeout=280)
print(f"\n=== 剧情分析构建 → HTTP {r.status_code}")
if r.status_code == 200:
    d = r.json()
    print(f"  state={d.get('state')} cache_hit={d.get('cache_hit')}")
    a = d.get("analysis") or {}
    print(f"  events={len(a.get('events') or [])} relations={len(a.get('relations') or [])} foreshadows={len(a.get('foreshadows') or [])}")
else:
    print("  ", r.text[:400])

# 3. 重新读剧情分析
r = requests.get(f"{BASE}/api/v1/agent/story-analysis", headers=H, params={"series_id": SID}, timeout=15)
show("剧情分析(构建后)", r)

# 4. 时间线 + 设定书
r = requests.get(f"{BASE}/api/v1/agent/timeline", headers=H, params={"series_id": SID}, timeout=15)
show("时间线", r)
r = requests.get(f"{BASE}/api/v1/agent/lorebook", headers=H, params={"series_id": SID}, timeout=15)
show("设定书", r)

# 5. GraphRAG 全局层
r = requests.get(f"{BASE}/api/v1/agent/rag-global", headers=H, params={"series_id": SID}, timeout=15)
show("GraphRAG", r)
