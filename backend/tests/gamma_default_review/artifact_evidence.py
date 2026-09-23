"""Emit content-free, comparable Fe2O3/NaCl pipeline artifact hashes."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.app.recipes.loader import RecipePackLoader  # noqa: E402
from backend.app.recipes.registry import DEFAULT_PACK_DIR  # noqa: E402
from backend.app.workflow.pipeline import WorkflowGenerationPipeline  # noqa: E402
from backend.tests.be_a import conftest as cases  # noqa: E402


def digest(data: bytes) -> dict[str, object]:
    return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def build() -> dict[str, object]:
    pack, recipes = RecipePackLoader().load_pack(DEFAULT_PACK_DIR)
    sources = {
        path.relative_to(ROOT).as_posix(): digest(path.read_bytes())
        for path in sorted(DEFAULT_PACK_DIR.rglob("*.yaml"))
    }
    assert len(sources) == 14 and len(recipes) == 13
    inputs = {
        "fe2o3": cases.fe2o3_request.__wrapped__(cases.fe2o3_structure.__wrapped__()),
        "nacl": cases.nacl_request.__wrapped__(cases.nacl_structure.__wrapped__()),
    }
    generated = {}
    for label, request in inputs.items():
        result = WorkflowGenerationPipeline().generate(request).bundle
        assert not any(Path(name).name.upper() == "POTCAR" for name in result.files)
        with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as archive:
            assert set(archive.namelist()) == set(result.files)
            for name, data in result.files.items():
                assert archive.read(name) == data
            entries = [
                {
                    "path": item.filename,
                    "create_system": item.create_system,
                    "date_time": list(item.date_time),
                    "external_attr": item.external_attr,
                    "compress_type": item.compress_type,
                    "crc32": item.CRC,
                }
                for item in archive.infolist()
            ]
        generated[label] = {
            "files": {name: digest(data) for name, data in sorted(result.files.items())},
            "zip": digest(result.zip_bytes),
            "zip_entries": entries,
        }
    return {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "os": platform.system(),
        "python_version": platform.python_version(),
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
        "recipe_source_files": sources,
        "recipe_pack_sha256": pack.sha256,
        "generated": generated,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote comparable Fe2O3/NaCl hash evidence: {output}")


if __name__ == "__main__":
    main()
