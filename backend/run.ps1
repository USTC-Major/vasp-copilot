# VASP-Doctor 一键启动（Windows PowerShell）
# 用法（在 backend 目录）:  powershell -ExecutionPolicy Bypass -File .\run.ps1
param(
    [string]$Listen = "127.0.0.1",
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# 若存在 .env，逐行加载为当前进程环境变量（忽略 # 注释）
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $kv = $line -split "=", 2
            [Environment]::SetEnvironmentVariable($kv[0].Trim(), $kv[1].Trim(), "Process")
        }
    }
    Write-Host "已加载 $envFile"
}

# 优先使用仓库内 .venv（D 盘），未创建则回退 PATH 中的 python
$venvPy = Join-Path $here "..\.venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $py = $venvPy
} else {
    $py = "python"
    Write-Host "未找到 .venv，回退 PATH 中的 python（建议先运行 backend\setup_env.ps1）"
}
Write-Host "使用 Python: $py"

Write-Host "启动 VASP-Doctor: http://$Listen`:$Port  (文档: http://$Listen`:$Port/api/v1/openapi.json)"
& $py -X utf8 -m uvicorn app.main:app --host $Listen --port $Port