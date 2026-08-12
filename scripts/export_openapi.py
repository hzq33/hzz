"""Export FastAPI OpenAPI schema to a frozen JSON contract file.

Usage:
    python scripts/export_openapi.py
    python scripts/export_openapi.py --out docs/openapi.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root is importable when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Fail-closed auth needs a token during import for protected-route metadata.
os.environ.setdefault("AGENT_API_TOKEN", "openapi-export-token")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3001")
os.environ.setdefault("DEEPSEEK_API_KEY", "openapi-export-placeholder")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Agent Server OpenAPI schema")
    parser.add_argument(
        "--out",
        type=Path,
        default=_ROOT / "docs" / "openapi.json",
        help="Output JSON path (default: docs/openapi.json)",
    )
    args = parser.parse_args()

    import agent_server

    schema = agent_server.app.openapi()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths = sorted((schema.get("paths") or {}).keys())
    print(f"Wrote {args.out} ({len(paths)} paths)")
    for path in paths:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
