#!/usr/bin/env bash
# VASP-Doctor 一键启动（Linux/macOS）。用法（在 backend 目录）:  ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "已加载 .env"
fi

echo "启动 VASP-Doctor: http://${HOST}:${PORT}  (文档: http://${HOST}:${PORT}/api/v1/openapi.json)"
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"