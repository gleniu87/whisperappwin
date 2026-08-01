<#
.SYNOPSIS
    Launches WhisperDictate from the project virtualenv.

.PARAMETER Hidden
    Run with pythonw.exe so no console window stays open. The tray icon is then
    the only UI; logs go to %APPDATA%\WhisperDictateWin\whisperdictate.log.

.PARAMETER Check
    Run environment diagnostics instead of starting the app.

.PARAMETER ListDevices
    List input devices and exit.

.PARAMETER Record
    Record N seconds from the microphone, print the transcript, and exit.

.PARAMETER Trace
    Enable DEBUG-level logging. (Named Trace, not Verbose, because CmdletBinding
    already reserves -Verbose.)

.EXAMPLE
    .\run.ps1
.EXAMPLE
    .\run.ps1 -Hidden
.EXAMPLE
    .\run.ps1 -Record 5
#>
[CmdletBinding()]
param(
    [switch]$Hidden,
    [switch]$Check,
    [switch]$ListDevices,
    [double]$Record = 0,
    [switch]$Trace
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$venvPythonw = Join-Path $root ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $venvPython)) {
    throw "Brak venv. Uruchom najpierw: .\setup.ps1"
}

$appArgs = @("-m", "whisperdictate")
if ($Trace)       { $appArgs += "--verbose" }
if ($Check)       { $appArgs += "--check" }
if ($ListDevices) { $appArgs += "--list-devices" }
if ($Record -gt 0){ $appArgs += @("--record", $Record) }

# The package is imported from the repo root, so run from there regardless of
# where the caller happened to be.
Push-Location $root
try {
    if ($Hidden -and -not ($Check -or $ListDevices -or $Record -gt 0)) {
        Start-Process -FilePath $venvPythonw -ArgumentList $appArgs -WindowStyle Hidden
        Write-Host "WhisperDictate uruchomiony w tle. Ikona w zasobniku systemowym." -ForegroundColor Green
    } else {
        & $venvPython @appArgs
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
