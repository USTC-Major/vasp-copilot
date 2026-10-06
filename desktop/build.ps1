[CmdletBinding()]
param(
    [ValidateSet('ci', 'offline', 'existing')][string]$FrontendDependencies = 'ci',
    [string]$SdkPackage = ''
)
. (Join-Path $PSScriptRoot 'common.ps1')
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskOutput = Join-Path $PSScriptRoot 'dist'
$taskManifest = Join-Path $taskOutput 'build-manifest.json'
# A failed rebuild must not leave a prior success manifest looking current.
if (Test-Path -LiteralPath $taskManifest -PathType Leaf) { Remove-Item -LiteralPath $taskManifest }
$taskCompiler = Get-DesktopCompiler
$taskSdk = & (Join-Path $PSScriptRoot 'restore-sdk.ps1') -PackagePath $SdkPackage
$taskIcon = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'assets/icon-export.json') -Raw | ConvertFrom-Json
Assert-DesktopHash (Join-Path $PSScriptRoot 'assets/app-icon.svg') $taskIcon.source_sha256
foreach ($taskFile in $taskIcon.outputs.PSObject.Properties) {
    Assert-DesktopHash (Join-Path $PSScriptRoot ('assets/' + $taskFile.Name)) $taskFile.Value
}
if (Get-ChildItem -LiteralPath (Join-Path $taskRoot 'frontend') -Filter '.env*' -Force) {
    throw 'Production desktop build requires a clean frontend without .env files; no user environment file is loaded.'
}
$taskNpm = (Get-Command npm.cmd -ErrorAction Stop).Source
$taskPreviousMocks = [Environment]::GetEnvironmentVariable('VITE_USE_MOCKS', 'Process')
try {
    $env:VITE_USE_MOCKS = 'false'
    Push-Location (Join-Path $taskRoot 'frontend')
    try {
        if ($FrontendDependencies -ne 'existing') {
            $taskNpmArguments = @('ci', '--ignore-scripts', '--no-audit', '--no-fund')
            if ($FrontendDependencies -eq 'offline') { $taskNpmArguments += '--offline' }
            & $taskNpm @taskNpmArguments
            if ($LASTEXITCODE -ne 0) { throw 'Locked frontend dependency restore failed.' }
        }
        & $taskNpm run build
        if ($LASTEXITCODE -ne 0) { throw 'Production frontend build failed.' }
    } finally { Pop-Location }
} finally { [Environment]::SetEnvironmentVariable('VITE_USE_MOCKS', $taskPreviousMocks, 'Process') }
New-Item -ItemType Directory -Force -Path $taskOutput | Out-Null
$taskExe = Join-Path $taskOutput 'VASP-Copilot-Desktop-V3.exe'
$taskSources = @((Join-Path $taskRoot 'launcher/windows/NativeProcess.cs'), (Join-Path $taskRoot 'launcher/windows/LauncherCore.cs'), (Join-Path $PSScriptRoot 'DesktopApp.cs'))
& $taskCompiler /nologo /target:winexe /platform:x64 /optimize+ /utf8output "/win32manifest:$PSScriptRoot/app.manifest" "/win32icon:$PSScriptRoot/assets/app-icon.ico" "/resource:$PSScriptRoot/assets/app-icon.png,VaspCopilot.Brand" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/reference:$taskSdk/lib/net462/Microsoft.Web.WebView2.Core.dll" "/reference:$taskSdk/lib/net462/Microsoft.Web.WebView2.WinForms.dll" "/out:$taskExe" $taskSources
if ($LASTEXITCODE -ne 0) { throw 'Desktop compilation failed.' }
foreach ($taskFile in @('lib/net462/Microsoft.Web.WebView2.Core.dll', 'lib/net462/Microsoft.Web.WebView2.WinForms.dll', 'runtimes/win-x64/native/WebView2Loader.dll')) {
    Copy-Item -LiteralPath (Join-Path $taskSdk $taskFile) -Destination $taskOutput -Force
    Assert-DesktopHash (Join-Path $taskOutput ([IO.Path]::GetFileName($taskFile))) (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'webview2.lock.json') -Raw | ConvertFrom-Json).files.$taskFile
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app.config') -Destination "$taskExe.config" -Force
foreach ($taskName in @('LICENSE.txt', 'NOTICE.txt')) {
    Copy-Item -LiteralPath (Join-Path $taskSdk $taskName) -Destination (Join-Path $taskOutput "WebView2-$taskName") -Force
}
$taskFiles = @{}
$taskDeliverables = @('VASP-Copilot-Desktop-V3.exe', 'VASP-Copilot-Desktop-V3.exe.config', 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll', 'WebView2-LICENSE.txt', 'WebView2-NOTICE.txt')
foreach ($taskName in $taskDeliverables) {
    $taskFiles[$taskName] = (Get-FileHash -LiteralPath (Join-Path $taskOutput $taskName) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$taskInputs = @{}
$taskInputNames = @('desktop/app.config', 'desktop/app.manifest', 'desktop/webview2.lock.json', 'desktop/build.ps1', 'desktop/common.ps1', 'desktop/restore-sdk.ps1', 'launcher/runtime.py', 'desktop/assets/app-icon.svg', 'desktop/assets/app-icon.png', 'desktop/assets/app-icon.ico', 'desktop/assets/icon-export.json', 'desktop/assets/export_icon.py', 'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html', 'frontend/vite.config.ts', 'frontend/tsconfig.json', 'frontend/tsconfig.app.json', 'frontend/tsconfig.node.json')
$taskInputPaths = @($taskSources) + @($taskInputNames | ForEach-Object { Join-Path $taskRoot $_ })
foreach ($taskDirectory in @('frontend/src', 'frontend/public')) {
    $taskInputPaths += @(Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskDirectory) -File -Recurse | Select-Object -ExpandProperty FullName)
}
foreach ($taskFile in $taskInputPaths) {
    $taskInputs[$taskFile.Substring($taskRoot.Length + 1).Replace('\', '/')] = (Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash.ToLowerInvariant()
}
$taskHead = 'source-export'
if (Test-Path -LiteralPath (Join-Path $taskRoot '.git')) { $taskHead = (& git -C $taskRoot rev-parse HEAD | Out-String).Trim() }
$taskFrontendFiles = @{}
foreach ($taskFile in (Get-ChildItem -LiteralPath (Join-Path $taskRoot 'frontend/dist') -File -Recurse)) {
    $taskFrontendFiles[$taskFile.FullName.Substring((Join-Path $taskRoot 'frontend/dist').Length + 1).Replace('\', '/')] = (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
[ordered]@{ utc = [DateTime]::UtcNow.ToString('o'); head = $taskHead; frontendMock = $false; frontendDependencies = $FrontendDependencies; sdkVersion = '1.0.3650.58'; compilerVersion = (Get-Item -LiteralPath $taskCompiler).VersionInfo.FileVersion; inputs = $taskInputs; output = $taskFiles; frontendDist = $taskFrontendFiles } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $taskManifest -Encoding UTF8
Get-FileHash -LiteralPath $taskExe -Algorithm SHA256
