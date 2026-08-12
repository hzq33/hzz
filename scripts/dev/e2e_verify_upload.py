"""E2E 验证：上传合成书 → 同步入库管线。"""
import json
import os
import sys

import requests

TOKEN = os.environ["AGENT_API_TOKEN"]
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}

with open("data/upload_tmp/e2e_verify_book.md", "rb") as f:
    files = {"file": ("e2e_verify_book.md", f, "text/markdown")}
    params = {
        "series_id": "e2e_verify_series",
        "series_title": "E2E验证系列",
        "volume_no": 1,
        "generate_qa": True,
        "generate_character_llm": True,
        "wait": True,
    }
    r = requests.post(f"{BASE}/api/v1/agent/upload", headers=H, files=files, params=params, timeout=280)
    print("HTTP", r.status_code)
    try:
        d = r.json()
        print(json.dumps(d, ensure_ascii=False, indent=1)[:3000])
    except Exception:
        print(r.text[:1000])
