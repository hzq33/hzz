"""Convert a real EPUB volume to markdown for seed-hybrid comparison analysis.

Reads the uploaded volume-07 epub and writes cleaned chapter text to
data/analysis_texts/ for prompt-candidate distribution checks.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def main() -> None:
    from src.application.novel.ingest import convert_to_md, preprocess_raw_md

    epub = next((ROOT / "data/upload_tmp").glob("*史莱姆*.epub"), None)
    if epub is None:
        print("NO_EPUB: no 史莱姆 epub under data/upload_tmp")
        return
    raw_md, toc = convert_to_md(epub.read_bytes(), epub.name, "application/epub+zip")
    cleaned = preprocess_raw_md(raw_md)
    out_dir = ROOT / "data/analysis_texts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vol07_cleaned.md").write_text(cleaned, encoding="utf-8")
    print(f"WROTE {out_dir / 'vol07_cleaned.md'} chars={len(cleaned):,} toc_len={len(toc or [])}")


if __name__ == "__main__":
    main()
