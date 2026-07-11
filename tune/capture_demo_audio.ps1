<#
.SYNOPSIS
Captures one temporary Windows microphone recording for the Gemma stage demo.

.DESCRIPTION
This helper is intentionally outside the frontend and corpus pipeline. It asks
Windows ffmpeg to capture a named DirectShow audio device and writes one 16 kHz
mono FLAC to an explicit operator-selected path. It refuses to overwrite files
and never writes under data/corpus or updates Postgres.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DeviceName,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [ValidateRange(1, 30)]
    [int]$DurationSeconds = 8
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue)) {
    throw "ffmpeg.exe is not available on the Windows PATH."
}

$resolvedParent = Resolve-Path (Split-Path -Parent $OutputPath)
$destination = Join-Path $resolvedParent (Split-Path -Leaf $OutputPath)
if (Test-Path $destination) {
    throw "Refusing to overwrite existing demo audio: $destination"
}

Write-Host "Recording temporary demo audio for $DurationSeconds seconds..."
& ffmpeg.exe `
    -hide_banner `
    -loglevel warning `
    -f dshow `
    -i "audio=$DeviceName" `
    -t $DurationSeconds `
    -ar 16000 `
    -ac 1 `
    -c:a flac `
    $destination

if ($LASTEXITCODE -ne 0 -or -not (Test-Path $destination)) {
    throw "Windows microphone capture failed."
}

Write-Host "Temporary demo recording created: $destination"
