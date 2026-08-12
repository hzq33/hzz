"""把 TXT 清洗后的 MD 落盘，供人工审阅结构。"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def main():
    from src.domain.novel.preprocessor import detect_and_transcode
    from src.application.novel.ingest import preprocess_raw_md

    src = ROOT / "data" / "re0 38.txt"
    dst = ROOT / "data" / "re0 38_cleaned.md"

    print(f"读取: {src}")
    raw_md, _ = detect_and_transcode(src.read_bytes())
    print(f"转码后字符数: {len(raw_md):,}")

    cleaned = preprocess_raw_md(raw_md)
    print(f"清洗后字符数: {len(cleaned):,}")

    dst.write_text(cleaned, encoding="utf-8")
    print(f"已保存: {dst}")
    print(f"文件大小: {dst.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
