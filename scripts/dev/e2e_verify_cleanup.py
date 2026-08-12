"""E2E 验证：清理合成测试系列（走正式 API，验证 C-03 删除链路）。"""
import json
import os
import requests

TOKEN = os.environ["AGENT_API_TOKEN"]
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}
SID = "e2e_verify_series"
DOC = "e2e_verify_series__vol01"

# C-03 删除卷（应联动 purge sidecar）
r = requests.delete(f"{BASE}/api/v1/agent/novels/{DOC}", headers=H, params={"series_id": SID}, timeout=60)
print("C-03 删除卷 → HTTP", r.status_code)
try:
    d = r.json()
    print("  deleted_blocks:", d.get("deleted_blocks"), "| purged:", json.dumps(d.get("purged"), ensure_ascii=False)[:200])
except Exception:
    print("  ", r.text[:300])

# 验证残留
import pathlib
print("\n残留检查:")
for pat in ("*e2e*", "*E2E*"):
    for root in ("data/catalogs", "data/rosters", "data/inventories", "data/graphs", "data/analysis", "data/lancedb"):
        for p in pathlib.Path(root).glob(pat):
            print(f"  ⚠ 残留: {root}/{p.name}")
print("  （以上为空则干净）")
