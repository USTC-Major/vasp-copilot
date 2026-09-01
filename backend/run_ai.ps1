# 启动智能模式后端（独立端口；内核进程内运行，无常驻）
# 用法（backend 目录下）: powershell -ExecutionPolicy Bypass -File .\run_ai.ps1
param(
    [int]$Port = 8500
)
$ErrorActionPreference = "Stop"
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m uvicorn ai_mode.server:app --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }