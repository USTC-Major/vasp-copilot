[CmdletBinding()]
param([string]$PackagePath = '')
. (Join-Path $PSScriptRoot 'common.ps1')
$taskLock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'webview2.lock.json') -Raw | ConvertFrom-Json
$taskCache = Join-Path $PSScriptRoot '.cache'
$taskArchive = Join-Path $taskCache "Microsoft.Web.WebView2.$($taskLock.version).nupkg"
$taskSdk = Join-Path $taskCache "WebView2-$($taskLock.version)"
New-Item -ItemType Directory -Force -Path $taskCache | Out-Null
if ($PackagePath) {
    Assert-DesktopHash $PackagePath $taskLock.sha256
    if ([IO.Path]::GetFullPath($PackagePath) -ne [IO.Path]::GetFullPath($taskArchive)) {
        Copy-Item -LiteralPath $PackagePath -Destination $taskArchive -Force
    }
}
if (!(Test-Path -LiteralPath $taskArchive -PathType Leaf)) {
    $taskPartial = Join-Path $taskCache ("sdk-download-" + [Guid]::NewGuid().ToString('N') + '.partial')
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $taskLock.url -OutFile $taskPartial -UseBasicParsing
        Assert-DesktopHash $taskPartial $taskLock.sha256
        Move-Item -LiteralPath $taskPartial -Destination $taskArchive
    } finally {
        if (Test-Path -LiteralPath $taskPartial -PathType Leaf) { Remove-Item -LiteralPath $taskPartial }
    }
}
# A cached package and every used extracted file are verified on every build.
Assert-DesktopHash $taskArchive $taskLock.sha256
if (!(Test-Path -LiteralPath $taskSdk -PathType Container)) {
    $taskExtract = Join-Path $taskCache ("sdk-extract-" + [Guid]::NewGuid().ToString('N'))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($taskArchive, $taskExtract)
    foreach ($taskFile in $taskLock.files.PSObject.Properties) {
        Assert-DesktopHash (Join-Path $taskExtract $taskFile.Name) $taskFile.Value
    }
    Move-Item -LiteralPath $taskExtract -Destination $taskSdk
}
foreach ($taskFile in $taskLock.files.PSObject.Properties) {
    Assert-DesktopHash (Join-Path $taskSdk $taskFile.Name) $taskFile.Value
}
Write-Output $taskSdk
