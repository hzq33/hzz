"""Probe the real LanceDB corpus to understand block distribution.

Reads ./data/novel_lance and prints per-doc, per-block_type counts plus a
few sample blocks per channel. Output informs the eval fixture sampling
strategy — we want real blocks, not synthetic ones.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

from src.application.novel.factory import create_novel_store


def main() -> None:
    store = create_novel_store(backend="lancedb", lance_path="./data/novel_lance")

    print("=" * 70)
    print(" Real LanceDB corpus probe")
    print("=" * 70)
    print(f"doc_ids: {store.doc_ids()}")
    print(f"total blocks: {store.block_count()}")

    for doc_id in store.doc_ids():
        print(f"\n--- doc_id={doc_id!r} ---")
        blocks = store.iter_blocks(doc_id=doc_id)
        by_type = Counter(b.block_type for b in blocks)
        print(f"  block_type distribution: {dict(by_type)}")

        # Sample 1 block per type for inspection
        seen_types: set[str] = set()
        for b in blocks:
            if b.block_type in seen_types:
                continue
            seen_types.add(b.block_type)
            print(f"\n  [{b.block_type}] global_id={b.global_id}")
            text_preview = (
                getattr(b, "narrative_text", "")
                or getattr(b, "vec_text_dialogue", "")
                or getattr(b, "vec_text_qa", "")
                or getattr(b, "vec_text_character", "")
                or ""
            )
            if text_preview:
                preview = text_preview.replace("\n", " ")[:120]
                print(f"    text: {preview}...")
            if b.block_type == "dialogue" and b.dialogues:
                d = b.dialogues[0]
                print(f"    dialogue[0]: {d.speaker}: {d.content[:60]}")
            if b.block_type == "qa":
                print(f"    Q: {b.question[:80]}")
                print(f"    A: {b.answer[:80]}")
            if b.block_type == "character":
                print(f"    name: {b.character_name}")
                print(f"    personality: {(b.personality or '')[:80]}")

    print("\n" + "=" * 70)
    print(" Sampling recommendations:")
    print(" - Aim for ~20 blocks per book (5 per channel) for fixture stability")
    print(" - Pick blocks whose narrative_text/question/answer are self-contained")
    print(" - Prefer chapters near the middle (intro/end chapters are atypical)")
    print("=" * 70)


if __name__ == "__main__":
    main()
