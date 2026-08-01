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
    [switch]$All,
    [double]$Record = 0,
    [string]$Device,
    [string]$SetApiKey,
    [string]$Enhance,
    [string]$Provider,
    [string]$Benchmark,
    [switch]$Quality,
    [switch]$SuggestVocabulary,
    [string]$AddVocabulary,
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
if ($All)         { $appArgs += "--all" }
if ($Record -gt 0){ $appArgs += @("--record", $Record) }
if ($Device)      { $appArgs += @("--device", $Device) }
if ($SetApiKey)   { $appArgs += @("--set-api-key", $SetApiKey) }
if ($Enhance)     { $appArgs += @("--enhance", $Enhance) }
if ($Provider)    { $appArgs += @("--provider", $Provider) }
if ($Benchmark)   { $appArgs += @("--benchmark", $Benchmark) }
if ($Quality)     { $appArgs += "--quality" }
if ($SuggestVocabulary) { $appArgs += "--suggest-vocabulary" }
if ($AddVocabulary)     { $appArgs += @("--add-vocabulary", $AddVocabulary) }

# The package is imported from the repo root, so run from there regardless of
# where the caller happened to be.
Push-Location $root
try {
    $isOneShot = $Check -or $ListDevices -or $Record -gt 0 -or $SetApiKey -or $Enhance -or $Benchmark -or $Quality -or $SuggestVocabulary -or $AddVocabulary
    if ($Hidden -and -not $isOneShot) {
        Start-Process -FilePath $venvPythonw -ArgumentList $appArgs -WindowStyle Hidden
        Write-Host "WhisperDictate uruchomiony w tle. Ikona w zasobniku systemowym." -ForegroundColor Green
    } else {
        & $venvPython @appArgs
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
