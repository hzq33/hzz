"""E2E 评估：上传短篇小说 → 完整入库管线。"""
import json
import os
import sys

import requests

TOKEN = os.environ["AGENT_API_TOKEN"]
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}
SID = "北境守望者"
DOC = f"{SID}__vol01"

with open("data/upload_tmp/e2e_story_book.md", "rb") as f:
    files = {"file": ("e2e_story_book.md", f, "text/markdown")}
    params = {
        "series_id": SID,
        "series_title": "北境守望者",
        "volume_no": 1,
        "generate_qa": True,
        "generate_character_llm": True,
        "wait": True,
    }
    r = requests.post(f"{BASE}/api/v1/agent/upload", headers=H, files=files, params=params, timeout=280)
    print("HTTP", r.status_code)
    try:
        d = r.json()
        print(json.dumps(d, ensure_ascii=False, indent=1)[:2000])
    except Exception:
        print(r.text[:800])
