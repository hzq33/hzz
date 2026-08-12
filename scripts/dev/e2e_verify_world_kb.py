"""E2E 验证 world_knowledge 工具 + verdict 回收（经真实 API）。"""
import json
import os
import sys

import requests

TOKEN = os.environ["AGENT_API_TOKEN"]
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}

# 1. 扮演雷姆问"你喜欢的人"——应触发 world_knowledge 查询 relations
print("=== 扮演雷姆：你喜欢的人 ===")
r = requests.post(
    f"{BASE}/api/v1/agent/impersonate/chat",
    headers=H,
    json={
        "series_id": "Re：从零开始的前日传 冰洁的羁绊",
        "character": "雷姆",
        "message": "你喜欢的人是谁？",
    },
    timeout=280,
)
print("HTTP", r.status_code)
try:
    d = r.json()
    print("回复:", str(d.get("reply") or "")[:200])
    print("引用数:", len(d.get("citations") or []))
    print("citations 通道:", set(c.get("channel") for c in (d.get("citations") or [])))
except Exception:
    print(r.text[:400])

# 2. 检查 /metrics 回收指标
print("\n=== /metrics 回收指标 ===")
r = requests.get(f"{BASE}/metrics", headers=H, timeout=15)
if r.status_code == 200:
    text = r.text
    for name in ("agent_retrieval_relevance_total", "agent_tool_value_total", "agent_answer_coverage_total"):
        hits = [l for l in text.splitlines() if name in l and "TYPE" not in l and "HELP" not in l]
        print(f"  {name}:")
        for h in hits[:5]:
            print("   ", h)
else:
    # 可能路径不同
    print("metrics HTTP", r.status_code, r.text[:100])
