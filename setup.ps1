<#
.SYNOPSIS
    One-time setup: creates a local virtualenv and installs dependencies.

.DESCRIPTION
    Deliberately uses a project-local .venv rather than the active Anaconda/system
    environment - faster-whisper pulls in ctranslate2 and ~600 MB of NVIDIA runtime
    wheels, which have no business in a shared base environment.

.PARAMETER Cpu
    Skip the CUDA runtime wheels even if an NVIDIA GPU is present.

.PARAMETER Python
    Interpreter used to create the venv. Defaults to whatever `python` resolves to.

.EXAMPLE
    .\setup.ps1
.EXAMPLE
    .\setup.ps1 -Cpu
#>
[CmdletBinding()]
param(
    [switch]$Cpu,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

# --- interpreter ------------------------------------------------------------

Write-Step "Sprawdzam interpreter Pythona"
$pythonCmd = Get-Command $Python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    throw "Nie znaleziono '$Python' w PATH. Zainstaluj Pythona 3.10+ albo podaj -Python <sciezka>."
}
$version = & $pythonCmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "    $($pythonCmd.Source) (Python $version)"

$major, $minor = $version.Split('.')
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
    throw "Wymagany Python 3.10 lub nowszy, znaleziono $version."
}

# --- virtualenv -------------------------------------------------------------

if (Test-Path $venvPython) {
    Write-Step "Uzywam istniejacego venv: $venv"
} else {
    Write-Step "Tworze venv: $venv"
    & $pythonCmd.Source -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Tworzenie venv nie powiodlo sie." }
}

Write-Step "Aktualizuje pip"
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "Aktualizacja pip nie powiodla sie." }

# --- dependencies -----------------------------------------------------------

Write-Step "Instaluje zaleznosci podstawowe"
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Instalacja zaleznosci nie powiodla sie." }

# --- CUDA -------------------------------------------------------------------

$hasNvidia = $false
if (-not $Cpu) {
    $gpus = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
    if ($gpus) {
        $hasNvidia = $true
        Write-Step "Wykryto GPU NVIDIA: $($gpus[0].Name)"
        Write-Host "    Instaluje biblioteki CUDA (cuBLAS + cuDNN, ~600 MB)"
        & $venvPython -m pip install -r (Join-Path $root "requirements-cuda.txt")
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Instalacja bibliotek CUDA nie powiodla sie - aplikacja zadziala na CPU."
        }
    } else {
        Write-Step "Brak GPU NVIDIA - pomijam biblioteki CUDA"
    }
} else {
    Write-Step "Wymuszono tryb CPU (-Cpu) - pomijam biblioteki CUDA"
}

# --- verify -----------------------------------------------------------------

Write-Step "Diagnostyka srodowiska"
& $venvPython -m whisperdictate --check
$checkExit = $LASTEXITCODE

Write-Host ""
if ($checkExit -eq 0) {
    Write-Host "Gotowe. Uruchom aplikacje: .\run.ps1" -ForegroundColor Green
    if (-not $hasNvidia -and -not $Cpu) {
        Write-Host "Uwaga: dziala na CPU - transkrypcja bedzie kilkukrotnie wolniejsza." -ForegroundColor Yellow
    }
} else {
    Write-Warning "Diagnostyka zglosila problem - zobacz komunikaty powyzej."
}
exit $checkExit
