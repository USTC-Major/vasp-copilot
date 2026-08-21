from __future__ import annotations

"""Export the FastAPI OpenAPI document to backend/openapi.json.

Usage (from backend/):  python scripts/export_openapi.py [out_path]
Frontends can derive TypeScript types from openapi.json (MVP 5.3 contract-first).
"""

import json
import sys
from pathlib import Path

# ``app.main`` resolves from backend/ (pytest conftest path), but running this
# script puts scripts/ (not backend/) on sys.path[0]. Insert backend root so the
# documented invocation works without setting PYTHONPATH manually.
_BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.main import app


def main() -> int:
    default = Path(__file__).resolve().parent.parent / "openapi.json"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    data = app.openapi()
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"openapi.json -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())