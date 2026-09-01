#!/usr/bin/env bash
# 启动智能模式后端（独立端口；内核进程内运行，无常驻）
# 用法（backend 目录下）: ./run_ai.sh [port]
set -e
PORT="${1:-8500}"
PY="$(dirname "$0")/../.venv/bin/python"
if [ ! -x "$PY" ]; then PY="python3"; fi
cd "$(dirname "$0")"
exec "$PY" -m uvicorn ai_mode.server:app --host 127.0.0.1 --port "$PORT"