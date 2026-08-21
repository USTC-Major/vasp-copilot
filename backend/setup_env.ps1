# VASP-Doctor 一键创建/更新后端环境（Windows PowerShell）
# 用法（仓库根目录）:  powershell -ExecutionPolicy Bypass -File backend\setup_env.ps1
# 可选参数 -BasePython 指定基础解释器（默认 D:\anaconda3\python.exe）
# 原则：venv、pip 缓存、临时目录全部位于仓库内（D 盘），不写 C 盘。
param(
    [string]$BasePython = "D:\anaconda3\python.exe"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path   # backend/
$root = Split-Path -Parent $here                          # 仓库根/
$venv = Join-Path $root ".venv"
$pipCache = Join-Path $root ".pip-cache"
$tmp = Join-Path $root ".tmp"
$py = Join-Path $venv "Scripts\python.exe"
$req = Join-Path $here "requirements.txt"

if (-not (Test-Path $BasePython)) {
    Write-Host "未找到 $BasePython，回退使用 PATH 中的 python"
    $BasePython = "python"
}
Write-Host "基础解释器: $BasePython"

# 1) 确保 D 盘目录存在，并把缓存/临时目录指到仓库内
New-Item -ItemType Directory -Force -Path $pipCache, $tmp | Out-Null
$env:PIP_CACHE_DIR = $pipCache
$env:TMP = $tmp
$env:TEMP = $tmp

# 2) 创建 venv（已存在则跳过）
if (-not (Test-Path $py)) {
    Write-Host "创建虚拟环境: $venv"
    & $BasePython -m venv --system-site-packages $venv
    if ($LASTEXITCODE -ne 0) { Write-Host "创建 venv 失败"; exit $LASTEXITCODE }
} else {
    Write-Host "复用已有虚拟环境: $venv"
}

# 3) 升级 pip 并安装依赖（幂等，可重复执行做增量更新）
Write-Host "升级 pip ..."
& $py -X utf8 -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "安装依赖 ($req) ..."
& $py -X utf8 -m pip install -r $req
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 4) 导入自检
Write-Host "== 导入自检 =="
& $py -X utf8 -c "import fastapi, uvicorn, pymatgen, multipart; from importlib.metadata import version; print('deps OK: fastapi', fastapi.__version__, '/ pymatgen', version('pymatgen'))"
if ($LASTEXITCODE -ne 0) { Write-Host "依赖导入自检失败"; exit $LASTEXITCODE }

Write-Host "环境就绪。启动: powershell -ExecutionPolicy Bypass -File backend\run.ps1"