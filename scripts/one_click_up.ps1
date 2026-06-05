# Video2Text 一键启动 — PowerShell 包装。
#
# Thin wrapper around the cross-platform Python launcher so that
# Windows users can run ``.\scripts\one_click_up.ps1`` instead of
# typing the Python path.
#
# Usage::
#
#     .\scripts\one_click_up.ps1                  # start (default = up)
#     .\scripts\one_click_up.ps1 up               # explicit up
#     .\scripts\one_click_up.ps1 stop             # stop a daemonised server
#     .\scripts\one_click_up.ps1 status           # show status
#     .\scripts\one_click_up.ps1 up --port 8080 --daemon
#
# All arguments are forwarded to ``one_click_up.py`` verbatim.
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher  = Join-Path $ScriptDir "one_click_up.py"

if (-not (Test-Path $Launcher)) {
    Write-Error "one_click_up.py not found next to this script: $Launcher"
    exit 2
}

# Resolve a Python interpreter: prefer 'py' (the Windows launcher) so
# that shebangs and PEP 394 conventions both work, then fall back to
# 'python' / 'python3' on PATH.
$candidates = @("py", "python", "python3")
$python = $null
foreach ($cand in $candidates) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) {
    Write-Error "No Python interpreter found. Install Python 3.8+ and ensure it is on PATH."
    exit 127
}

& $python $Launcher @Args
exit $LASTEXITCODE
