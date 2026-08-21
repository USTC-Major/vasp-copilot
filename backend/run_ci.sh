#!/usr/bin/env bash
# VASP-Doctor 后端本地 CI 检查（Linux/macOS）。用法（backend 目录）:  ./run_ci.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p tests/.tmp
export PYTHONPATH="$PWD"
export TMP="$PWD/tests/.tmp"
export TEMP="$PWD/tests/.tmp"
PY=python
if [[ -n "${PYTHON:-}" ]]; then PY="$PYTHON"; fi

echo "== pytest =="
"$PY" -B -m pytest -q tests -p no:cacheprovider

echo "== export openapi =="
"$PY" -B scripts/export_openapi.py

echo "== smoke test =="
"$PY" -B scripts/smoke_test.py

echo "CI OK"