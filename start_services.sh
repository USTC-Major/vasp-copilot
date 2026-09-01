#!/usr/bin/env bash
# VASP-Doctor 本地开发服务启动器（Linux / macOS；Windows 用 start_services.ps1）
#
# 用法：
#   bash start_services.sh            # 一次性补齐缺失服务（8000/8500/5173）
#   bash start_services.sh --status   # 仅查看状态
#   bash start_services.sh --watch    # 守护模式：每 30s 检查，掉线自动拉起
#
# 端口：智能模式后端 8500 | 工具箱主后端 8000 | 前端 Vite 5173
# 日志：根目录 *.log / *.err.log

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
LOG="$ROOT/start_services.log"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
NODE="$(command -v node || true)"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

port_up() {
  # 用 HTTP 探测（比 ss/lsof 更通用）；1s 超时
  curl -s -o /dev/null -m 1 "http://127.0.0.1:$1$2" 2>/dev/null
}

wait_port() { # port path timeout_s
  local n=0
  while [ "$n" -lt "$(( $3 * 2 ))" ]; do
    port_up "$1" "$2" && return 0
    sleep 0.5; n=$((n + 1))
  done
  port_up "$1" "$2"
}

load_dotenv() {
  local f="$BACKEND_DIR/.env"
  [ -f "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    case "$line" in *=*) export "${line%%=*}"="${line#*=}" ;; esac
  done < "$f"
}

start_backend() {
  port_up 8000 /health && return 0
  log "启动工具箱主后端 8000 ..."
  load_dotenv
  (cd "$BACKEND_DIR" && nohup "$PY" -X utf8 -m uvicorn app.main:app \
      --host 127.0.0.1 --port 8000 \
      > "$ROOT/app_8000.log" 2> "$ROOT/app_8000.err.log" &)
  wait_port 8000 /health 25
}

start_ai() {
  port_up 8500 /docs && return 0
  log "启动智能模式后端 8500 ..."
  load_dotenv
  export ENABLE_AI_MODE=true
  (cd "$BACKEND_DIR" && nohup "$PY" -m uvicorn ai_mode.server:app \
      --host 127.0.0.1 --port 8500 --log-level info \
      > "$ROOT/ai_mode_8500.log" 2> "$ROOT/ai_mode_8500.err.log" &)
  wait_port 8500 /docs 25
}

start_frontend() {
  [ -n "$NODE" ] || NODE="$HOME/.easy-ai/runtime/node/node"
  [ -x "$NODE" ] || { log "未找到 node，跳过前端"; return 1; }
  port_up 5173 / && return 0
  log "启动前端 Vite 5173 ..."
  (cd "$FRONTEND_DIR" && nohup "$NODE" node_modules/vite/bin/vite.js \
      > "$ROOT/vite_dev.log" 2> "$ROOT/vite_dev.err.log" &)
  wait_port 5173 / 25
}

status() {
  for spec in "前端 5173 /" "主后端 8000 /health" "智能模式 8500 /docs"; do
    set -- $spec
    if port_up "$2" "$3"; then echo "$1 $2: UP"; else echo "$1 $2: DOWN"; fi
  done
}

start_all() {
  start_backend || log "主后端 8000 启动失败（见 app_8000.err.log）"
  start_ai || log "智能模式后端 8500 启动失败（见 ai_mode_8500.err.log）"
  start_frontend || log "前端 5173 启动失败（见 vite_dev.err.log）"
  status
}

case "${1:-}" in
  --status|-Status) status ;;
  --watch|-Watch)
    log "守护模式启动（每 30s 检查 8000/8500/5173，掉线自动拉起）"
    while true; do start_all >/dev/null 2>&1; sleep 30; done ;;
  *) start_all ;;
esac
