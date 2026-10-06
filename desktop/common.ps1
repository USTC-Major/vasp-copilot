Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-DesktopCompiler {
    if ($env:OS -ne 'Windows_NT') { throw 'Desktop build/tests require 64-bit Windows with .NET Framework 4.8.' }
    $taskFramework = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -Name Release
    if ($taskFramework.Release -lt 528040) { throw 'Existing .NET Framework 4.8 or later is required; this script does not install it.' }
    $taskCompiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if (!(Test-Path -LiteralPath $taskCompiler -PathType Leaf)) { throw 'The existing .NET Framework x64 C# compiler is missing.' }
    return $taskCompiler
}

function Assert-DesktopHash([string]$Path, [string]$Expected) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required file missing: $Path" }
    $taskActual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($taskActual -ne $Expected) { throw "SHA256 mismatch: $Path" }
}
