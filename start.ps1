# Startup script for DIVA — Data Intelligence Virtual Assistant
# PowerShell version — sets up the environment and runs the FastAPI application

# Stop on any error
$ErrorActionPreference = "Stop"

# ── Color helpers ───────────────────────────────────────────────────────────

function Write-Banner {
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host "  |                                                          |" -ForegroundColor Cyan
    Write-Host "  |        DIVA                                              |" -ForegroundColor Cyan
    Write-Host "  |                                                          |" -ForegroundColor Cyan
    Write-Host "  |        Data Intelligence Virtual Assistant              |" -ForegroundColor Cyan
    Write-Host "  |        Enterprise Multi-Agent Chat System               |" -ForegroundColor Cyan
    Write-Host "  |                                                          |" -ForegroundColor Cyan
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] " -ForegroundColor Blue -NoNewline
    Write-Host $Message
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] " -ForegroundColor Red -NoNewline
    Write-Host $Message
}

function Write-Step {
    param([string]$Message)
    Write-Host "> " -ForegroundColor Magenta -NoNewline
    Write-Host $Message -ForegroundColor White
}

# Print banner
Write-Banner

# ── System information ─────────────────────────────────────────────────────

Write-Step "System Information"

try {
    $pythonVersion = python --version 2>&1
    Write-Info "Python: $pythonVersion"
} catch {
    Write-Error "Python not found in PATH"
    exit 1
}

Write-Info "Working Directory: $(Get-Location)"
Write-Info "User: $env:USERNAME"
Write-Host ""

# ── Locate diva package ────────────────────────────────────────────────────

function Find-PythonPath {
    $locations = @(
        ".\src\diva",
        "..\src\diva",
        "src\diva",
        "$PSScriptRoot\src\diva",
        "C:\src\diva",
        "D:\src\diva"
    )

    foreach ($loc in $locations) {
        if (Test-Path $loc) {
            return Split-Path $loc -Parent
        }
    }

    return $null
}

# ── Environment setup ──────────────────────────────────────────────────────

Write-Step "Environment Setup"
$AppPath = Find-PythonPath

if ($AppPath) {
    Write-Success "Located package: $AppPath\diva"

    if ($env:PYTHONPATH) {
        $env:PYTHONPATH = $AppPath + ";" + $env:PYTHONPATH
        Write-Info "PYTHONPATH updated (appended to existing)"
    } else {
        $env:PYTHONPATH = $AppPath
        Write-Info "PYTHONPATH initialized"
    }

    Write-Host "[INFO] " -ForegroundColor Blue -NoNewline
    Write-Host "PYTHONPATH: $env:PYTHONPATH" -ForegroundColor DarkGray
} else {
    Write-Warning "Could not locate diva package in standard paths"
    Write-Info "Checking directory structure..."

    Get-ChildItem -Path . -Directory -ErrorAction SilentlyContinue | Select-Object Name | Format-Table -AutoSize

    if (Test-Path "src") {
        Get-ChildItem -Path ".\src" -Directory -ErrorAction SilentlyContinue | Select-Object Name | Format-Table -AutoSize
    } else {
        Write-Warning "No src directory found"
    }

    Write-Warning "Attempting to continue anyway..."
}

Write-Host ""

# ── Activate venv if available ─────────────────────────────────────────────

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
    Write-Info "Activated virtualenv: .venv"
} elseif (Test-Path ".\venv\Scripts\Activate.ps1") {
    & ".\venv\Scripts\Activate.ps1"
    Write-Info "Activated virtualenv: venv"
}

# ── Module verification ────────────────────────────────────────────────────

Write-Step "Module Verification"

try {
    python -c "import diva; print('Module import successful')" 2>&1 | Out-Null

    if ($LASTEXITCODE -eq 0) {
        Write-Success "Module import successful"
    } else {
        throw "Import failed"
    }
} catch {
    Write-Error "Module import failed!"
    Write-Error "PYTHONPATH: $env:PYTHONPATH"
    Write-Host ""
    exit 1
}

try {
    $modulePath = python -c "import diva; print(diva.__file__)" 2>&1
    Write-Host "[INFO] " -ForegroundColor Blue -NoNewline
    Write-Host "Module location: $modulePath" -ForegroundColor DarkGray
} catch {
    Write-Warning "Could not determine module location"
}

# ── Server config ──────────────────────────────────────────────────────────

$DivaHost = if ($env:DIVA_HOST) { $env:DIVA_HOST } else { "0.0.0.0" }
$DivaPort = if ($env:DIVA_PORT) { $env:DIVA_PORT } else { "8000" }

Write-Host ""
Write-Success "Pre-flight checks complete!"
Write-Info "Host: $DivaHost"
Write-Info "Port: $DivaPort"
Write-Host ""
Write-Host "  LAUNCHING DIVA  " -BackgroundColor Blue -ForegroundColor White
Write-Host ""

# ── Start the application ──────────────────────────────────────────────────

# Pass all command-line arguments to uvicorn; defaults are sensible for prod.
python -m uvicorn diva.main:app --host $DivaHost --port $DivaPort $args
