<#
.SYNOPSIS
    Offline Chinese/English TTS via Windows SAPI (System.Speech).

.DESCRIPTION
    MediaScribe's benchmark corpus must be reproducible with **no network access**
    (see .gitignore note: real corpus is normally published on HuggingFace, which is
    unreachable in this environment).  Windows ships Mandarin and English SAPI
    voices, so we can synthesise a real speech corpus locally and deterministically.

    Text is read from a UTF-8 file rather than passed on the command line to avoid
    shell quoting / console codepage corruption of CJK text.

.PARAMETER Voice
    SAPI voice name, e.g. 'Microsoft Huihui Desktop'.

.PARAMETER TextFile
    Path to a UTF-8 text file to speak.

.PARAMETER OutFile
    Path of the WAV file to write (22050 Hz, 16-bit, mono by default).

.PARAMETER Rate
    SAPI speaking rate, -10 (slowest) .. 10 (fastest).  Default 0.

.EXAMPLE
    pwsh -NoProfile -File sapi_tts.ps1 -Voice 'Microsoft Huihui Desktop' `
        -TextFile .\text.txt -OutFile .\out.wav
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Voice,
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [int]$Rate = 0
)

$ErrorActionPreference = 'Stop'

# System.Speech is a Windows-only .NET assembly.  Present on .NET Framework
# (powershell.exe 5.1) and loadable from PowerShell 7 via Add-Type.
Add-Type -AssemblyName System.Speech

$resolvedText = (Resolve-Path -LiteralPath $TextFile).Path
$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

# Read as UTF-8 explicitly: default encoding differs between powershell.exe
# (ANSI) and pwsh (UTF-8), and CJK text must survive either way.
$text = [System.IO.File]::ReadAllText($resolvedText, [System.Text.Encoding]::UTF8)
if ([string]::IsNullOrWhiteSpace($text)) {
    Write-Error "Text file is empty: $resolvedText"
    exit 1
}

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.SelectVoice($Voice)
    $synth.Rate = $Rate
    # Discard any audio the voice may have queued from a previous run.
    $synth.SetOutputToNull()
    $synth.SetOutputToWaveFile($OutFile)
    $synth.Speak($text)
    $synth.SetOutputToNull()
}
finally {
    $synth.Dispose()
}

$len = (Get-Item -LiteralPath $OutFile).Length
Write-Output "[sapi-tts] voice='$Voice' rate=$Rate bytes=$len out='$OutFile'"
exit 0
