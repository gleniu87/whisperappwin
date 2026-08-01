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

Write-Step "Checking the Python interpreter"
$pythonCmd = Get-Command $Python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    throw "'$Python' not found in PATH. Install Python 3.10+ or pass -Python <path>."
}
$version = & $pythonCmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "    $($pythonCmd.Source) (Python $version)"

$major, $minor = $version.Split('.')
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
    throw "Python 3.10 or newer is required, found $version."
}

# --- virtualenv -------------------------------------------------------------

if (Test-Path $venvPython) {
    Write-Step "Using the existing venv: $venv"
} else {
    Write-Step "Creating the venv: $venv"
    & $pythonCmd.Source -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Creating the venv failed." }
}

Write-Step "Upgrading pip"
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "Upgrading pip failed." }

# --- dependencies -----------------------------------------------------------

Write-Step "Installing the base dependencies"
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Installing the dependencies failed." }

# --- CUDA -------------------------------------------------------------------

$hasNvidia = $false
if (-not $Cpu) {
    $gpus = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
    if ($gpus) {
        $hasNvidia = $true
        Write-Step "Detected an NVIDIA GPU: $($gpus[0].Name)"
        Write-Host "    Installing the CUDA libraries (cuBLAS + cuDNN, ~600 MB)"
        & $venvPython -m pip install -r (Join-Path $root "requirements-cuda.txt")
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Installing the CUDA libraries failed - the app will run on CPU."
        }
    } else {
        Write-Step "No NVIDIA GPU - skipping the CUDA libraries"
    }
} else {
    Write-Step "CPU mode forced (-Cpu) - skipping the CUDA libraries"
}

# --- verify -----------------------------------------------------------------

Write-Step "Environment diagnostics"
& $venvPython -m whisperdictate --check
$checkExit = $LASTEXITCODE

Write-Host ""
if ($checkExit -eq 0) {
    Write-Host "Done. Start the app with: .\run.ps1" -ForegroundColor Green
    if (-not $hasNvidia -and -not $Cpu) {
        Write-Host "Note: running on CPU - transcription will be several times slower." -ForegroundColor Yellow
    }
} else {
    Write-Warning "Diagnostics reported a problem - see the messages above."
}
exit $checkExit
