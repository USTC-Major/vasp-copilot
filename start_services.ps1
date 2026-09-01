# VASP-Doctor 本地开发服务自愈启动器（Windows PowerShell）
#
# 职责：
#   1) 一键拉起三个本地服务（缺哪个补哪个，已就绪则跳过）：
#      - 智能模式后端  127.0.0.1:8500  (uvicorn ai_mode.server:app, ENABLE_AI_MODE=true)
#      - 工具箱主后端  127.0.0.1:8000  (uvicorn app.main:app)
#      - 前端 Vite     127.0.0.1:5173  (node node_modules/vite/bin/vite.js)
#   2) -Watch 守护：周期检查，进程掉了自动重新拉起（配合开机自启即可常驻）
#   3) HKCU 开机自启注册/移除（登录时自动进入守护模式）
#
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File .\start_services.ps1
#   powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -Status
#   powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -Watch
#   powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -InstallAutoStart
#   powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -RemoveAutoStart
#
# 日志：start_services.log（守护/启动记录）；各服务 stdout/stderr 见对应 *.log / *.err.log。

[CmdletBinding()]
param(
    [switch]$Status,              # 仅查看状态（不启动/不写注册表）
    [switch]$Watch,               # 守护模式：周期检查，掉线自动拉起
    [int]$WatchIntervalSeconds = 30,
    [switch]$InstallAutoStart,    # 注册 HKCU 开机自启
    [switch]$RemoveAutoStart      # 移除开机自启
)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { $Py = "python" }
$AutoStartValueName = "VASPDoctor_Services"

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Write-Log([string]$Message) {
    $line = "[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $Message
    Write-Host $line
    try { Add-Content -LiteralPath (Join-Path $Root "start_services.log") -Value $line -Encoding UTF8 } catch { }
}

function Load-DotEnv([string]$File) {
    if (-not (Test-Path -LiteralPath $File)) { return }
    Get-Content $File | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $kv = $line -split "=", 2
            [Environment]::SetEnvironmentVariable($kv[0].Trim(), $kv[1].Trim(), "Process")
        }
    }
}

function Start-Redirected([string]$FilePath, [string[]]$ArgumentList, [string]$WorkingDirectory, [string]$OutFile, [string]$ErrFile) {
    Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Root $OutFile) `
        -RedirectStandardError (Join-Path $Root $ErrFile) -PassThru | Out-Null
}

function Wait-Port([int]$Port, [int]$TimeoutSeconds = 25) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return (Test-PortListening $Port)
}

function Start-MainBackend {
    $Port = 8000
    if (Test-PortListening $Port) { return $true }
    Write-Log "启动工具箱主后端 8000 ..."
    Load-DotEnv (Join-Path $BackendDir ".env")
    Start-Redirected -FilePath $Py -ArgumentList @("-X", "utf8", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $Port.ToString()) `
        -WorkingDirectory $BackendDir -OutFile "app_8000.log" -ErrFile "app_8000.err.log"
    return (Wait-Port $Port)
}

function Start-AIBackend {
    $Port = 8500
    if (Test-PortListening $Port) { return $true }
    Write-Log "启动智能模式后端 8500 ..."
    Load-DotEnv (Join-Path $BackendDir ".env")
    $env:ENABLE_AI_MODE = "true"   # 在 .env 之后设置，保证智能模式开关为开
    Start-Redirected -FilePath $Py -ArgumentList @("-m", "uvicorn", "ai_mode.server:app", "--host", "127.0.0.1", "--port", $Port.ToString(), "--log-level", "info") `
        -WorkingDirectory $BackendDir -OutFile "ai_mode_8500.log" -ErrFile "ai_mode_8500.err.log"
    return (Wait-Port $Port)
}

function Start-Frontend {
    $Port = 5173
    if (Test-PortListening $Port) { return $true }
    Write-Log "启动前端 Vite 5173 ..."
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if (-not $node) { $node = Join-Path $env:USERPROFILE ".easy-ai\runtime\node\node.exe" }
    $viteJs = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $viteJs)) {
        Write-Log ("前端启动项缺失（node={0} vite={1}），跳过" -f $node, $viteJs)
        return $false
    }
    Start-Redirected -FilePath $node -ArgumentList @($viteJs) `
        -WorkingDirectory $FrontendDir -OutFile "vite_dev.log" -ErrFile "vite_dev.err.log"
    return (Wait-Port $Port)
}

function Test-AutoStart {
    $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    return (Get-ItemProperty -Path $key -Name $AutoStartValueName -ErrorAction SilentlyContinue).$AutoStartValueName
}

function Install-AutoStart {
    $ps1 = Join-Path $Root "start_services.ps1"
    $value = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ps1`" -Watch"
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name $AutoStartValueName -Value $value -Type String
    Write-Log ("已注册开机自启（登录即进入守护）：{0}" -f $value)
}

function Remove-AutoStart {
    Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name $AutoStartValueName -ErrorAction SilentlyContinue
    Write-Log "已移除开机自启"
}

function Print-Status {
    $fe = if (Test-PortListening 5173) { "UP" } else { "DOWN" }
    $main = if (Test-PortListening 8000) { "UP" } else { "DOWN" }
    $ai = if (Test-PortListening 8500) { "UP" } else { "DOWN" }
    Write-Host ("前端 5173: {0} | 工具箱主后端 8000: {1} | 智能模式后端 8500: {2}" -f $fe, $main, $ai)
    $as = Test-AutoStart
    if ($as) { Write-Host ("开机自启: 已注册 -> {0}" -f $as) } else { Write-Host "开机自启: 未注册" }
}

if ($RemoveAutoStart) { Remove-AutoStart }
if ($InstallAutoStart) { Install-AutoStart }
if ($Status) { Print-Status; exit 0 }
if ($InstallAutoStart -or $RemoveAutoStart) {
    if (-not $Watch) { exit 0 }
}

if ($Watch) {
    # 单实例守护：已有守护进程则直接退出，避免重复拉起
    $named = New-Object System.Threading.Mutex($false, "Local\VASPDoctorStartServices")
    $acquired = $false
    try {
        $acquired = $named.WaitOne(0)
    } catch { $acquired = $true }
    if (-not $acquired) { Write-Host "已有守护进程在运行，本次退出。"; exit 0 }
    Write-Log ("守护模式启动（每 {0}s 检查 8000/8500/5173，掉线自动拉起）" -f $WatchIntervalSeconds)
    try {
        while ($true) {
            if (-not (Start-MainBackend)) { Write-Log "主后端 8000 启动失败或恢复失败（见 app_8000.err.log）" }
            if (-not (Start-AIBackend)) { Write-Log "智能模式后端 8500 启动失败或恢复失败（见 ai_mode_8500.err.log）" }
            if (-not (Start-Frontend)) { Write-Log "前端 5173 启动失败或恢复失败（见 vite_dev.err.log）" }
            Start-Sleep -Seconds $WatchIntervalSeconds
        }
    } finally {
        try { $named.ReleaseMutex() } catch { }
    }
}

# 默认：一次性补齐缺失服务
if (-not (Start-MainBackend)) { Write-Log "主后端 8000 启动失败（见 app_8000.err.log）" }
if (-not (Start-AIBackend)) { Write-Log "智能模式后端 8500 启动失败（见 ai_mode_8500.err.log）" }
if (-not (Start-Frontend)) { Write-Log "前端 5173 启动失败（见 vite_dev.err.log）" }
Print-Status