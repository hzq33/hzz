"""Preview the sampled real_corpus.json fixture for case authoring."""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
data = json.loads((ROOT / "tests" / "eval" / "fixtures" / "real_corpus.json").read_text(encoding="utf-8"))

print(f"Total blocks: {len(data['blocks'])}")
print(f"Channels: {data['channels_present']}\n")

for i, b in enumerate(data["blocks"]):
    bt = b["block_type"][0].upper()
    gid_short = b["global_id"].split("__")[-1]  # vol05_c000_n0000
    ch = b.get("chapter_title", "")[:35]
    if b["block_type"] == "narrative":
        text = (b.get("narrative_text") or "").replace("\n", " ")
        preview = text[:140]
    else:
        ds = b.get("dialogues", [])
        preview = " | ".join(f"{d['speaker']}:{d['content']}" for d in ds[:3])[:140]
    print(f"[{i:02d}] {bt} {gid_short}  ch={ch}")
    print(f"     {preview}")
