[CmdletBinding()]
param([string]$OutputDirectory = '')
. (Join-Path $PSScriptRoot 'common.ps1')
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskBuild = Join-Path $PSScriptRoot 'dist'
$taskManifest = Get-Content -LiteralPath (Join-Path $taskBuild 'build-manifest.json') -Raw | ConvertFrom-Json
if (!$OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot ('.cache/packages/package-' + [Guid]::NewGuid().ToString('N')) }
if (![IO.Path]::IsPathRooted($OutputDirectory)) { throw 'Package output must be an absolute directory.' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Package output already exists; choose a new directory. Existing files are never cleared.' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
# Only version-controlled release inputs are eligible. Never copy local state or caches.
if (Test-Path -LiteralPath (Join-Path $taskRoot '.git')) {
    $taskSources = @(& git -c "safe.directory=$($taskRoot.Replace('\','/'))" -C $taskRoot -c core.quotepath=false ls-files --cached)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot enumerate version-controlled package inputs.' }
} else {
    $taskSources = @(Get-Content -LiteralPath (Join-Path $taskRoot 'SHA256SUMS.txt') | ForEach-Object { if ($_ -match '^[0-9a-f]{64}  (.+)$') { $Matches[1] } })
    if (!$taskSources.Count) { throw 'Source export lacks a release file inventory.' }
}
foreach ($taskRelative in $taskSources) {
    if ([IO.Path]::IsPathRooted($taskRelative) -or $taskRelative.Split('/') -contains '..') { throw 'Invalid package input path.' }
    if ($taskRelative -match '(^|/)\.env($|\.)' -and $taskRelative -notmatch '\.env\.(example|sample)$') { continue }
    if ($taskRelative -match '(^|/)(node_modules|dist|\.cache|__pycache__)(/|$)') { continue }
    if ($taskRelative -match '(\.exe$|\.dll$|\.pdb$|\.lnk$)' -or $taskRelative -eq '启动完整功能.cmd') { continue }
    $taskSource = Join-Path $taskRoot $taskRelative
    if (!(Test-Path -LiteralPath $taskSource -PathType Leaf)) { continue }
    $taskDestination = Join-Path $OutputDirectory $taskRelative
    New-Item -ItemType Directory -Force -Path (Split-Path $taskDestination -Parent) | Out-Null
    Copy-Item -LiteralPath $taskSource -Destination $taskDestination
}
foreach ($taskEntry in $taskManifest.output.PSObject.Properties) {
    Assert-DesktopHash (Join-Path $taskBuild $taskEntry.Name) $taskEntry.Value
    Copy-Item -LiteralPath (Join-Path $taskBuild $taskEntry.Name) -Destination (Join-Path $OutputDirectory $taskEntry.Name)
}
foreach ($taskEntry in $taskManifest.frontendDist.PSObject.Properties) {
    $taskSource = Join-Path $taskRoot ('frontend/dist/' + $taskEntry.Name)
    Assert-DesktopHash $taskSource $taskEntry.Value
    $taskDestination = Join-Path $OutputDirectory ('frontend/dist/' + $taskEntry.Name)
    New-Item -ItemType Directory -Force -Path (Split-Path $taskDestination -Parent) | Out-Null
    Copy-Item -LiteralPath $taskSource -Destination $taskDestination
}
foreach ($taskRelative in @('VASP-Copilot.exe', 'VASP-Copilot.exe.config', 'launcher/runtime.py', 'launcher/environment.py', 'launcher/python-support.json', 'frontend/dist/index.html', 'backend/app/main.py', 'backend/ai_mode/server.py', 'backend/requirements-win-cp311-x64.lock', 'backend/requirements-win-cp312-x64.lock', 'backend/requirements-win-cp313-x64.lock', 'backend/requirements-win-cp314-x64.lock')) {
    if (!(Test-Path -LiteralPath (Join-Path $OutputDirectory $taskRelative) -PathType Leaf)) { throw "Package missing: $taskRelative" }
}
Copy-Item -LiteralPath (Join-Path $taskBuild 'build-manifest.json') -Destination (Join-Path $OutputDirectory 'build-manifest.json')
$taskFiles = @{}
foreach ($taskFile in Get-ChildItem -LiteralPath $OutputDirectory -File -Recurse) {
    $taskFiles[$taskFile.FullName.Substring($OutputDirectory.Length + 1).Replace('\','/')] = (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
[ordered]@{ head = $taskManifest.head; entry = 'VASP-Copilot.exe'; files = $taskFiles } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'package-manifest.json') -Encoding UTF8
[ordered]@{ directory = $OutputDirectory; manifest = 'package-manifest.json' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskBuild 'last-package.json') -Encoding UTF8
Write-Output "Root EXE package: $OutputDirectory"
