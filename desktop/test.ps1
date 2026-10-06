[CmdletBinding()]
param([string]$PythonExecutable = 'python.exe', [string]$OutputDirectory = '')
. (Join-Path $PSScriptRoot 'common.ps1')
$taskCompiler = Get-DesktopCompiler
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = (Get-Command $PythonExecutable -ErrorAction Stop).Source
if (!$OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot '.cache/test-runs' }
if (![IO.Path]::IsPathRooted($OutputDirectory)) { throw 'Test output must be an absolute, test-only directory.' }
$taskBin = Join-Path $PSScriptRoot '.cache/test-bin'
New-Item -ItemType Directory -Force -Path $taskBin, $OutputDirectory | Out-Null
$taskSources = @((Join-Path $taskRoot 'launcher/windows/NativeProcess.cs'), (Join-Path $taskRoot 'launcher/windows/LauncherCore.cs'))
foreach ($taskName in @('ControllerHarness', 'ControllerHarnessLifecycle')) {
    $taskSource = Join-Path $PSScriptRoot "tests/$taskName.cs"
    & $taskCompiler /nologo /utf8output /target:exe /platform:x64 /reference:System.Web.Extensions.dll "/out:$taskBin/$taskName.exe" $taskSources $taskSource
    if ($LASTEXITCODE -ne 0) { throw "Test harness build failed: $taskName" }
}
$taskPreviousOutput = [Environment]::GetEnvironmentVariable('VASP_DESKTOP_TEST_OUTPUT', 'Process')
$taskPreviousUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
try {
    $env:VASP_DESKTOP_TEST_OUTPUT = $OutputDirectory
    $env:PYTHONUTF8 = '1'
    foreach ($taskName in @('risk_tests.py', 'additional_tests.py', 'lifecycle_tests.py')) {
        & $taskPython -X utf8 (Join-Path $PSScriptRoot "tests/$taskName")
        if ($LASTEXITCODE -ne 0) { throw "Isolated controller tests failed: $taskName" }
    }
} finally {
    [Environment]::SetEnvironmentVariable('VASP_DESKTOP_TEST_OUTPUT', $taskPreviousOutput, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', $taskPreviousUtf8, 'Process')
}
