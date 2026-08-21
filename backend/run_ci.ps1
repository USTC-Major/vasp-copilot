# VASP-Doctor 后端本地 CI 检查（Windows PowerShell）
# 用法（backend 目录）:  powershell -ExecutionPolicy Bypass -File .\run_ci.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$tmp = Join-Path $here "tests\.tmp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:PYTHONPATH = $here
$env:TMP = $tmp
$env:TEMP = $tmp
$py = $env:PYTHON
if (-not $py) {
    $venvPy = Join-Path $here "..\.venv\Scripts\python.exe"
    if (Test-Path $venvPy) { $py = $venvPy } else { $py = "python" }
}

Write-Host "== pytest =="
& $py -B -m pytest -q tests -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== export openapi =="
& $py -B scripts\export_openapi.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== smoke test =="
& $py -B scripts\smoke_test.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "CI OK"