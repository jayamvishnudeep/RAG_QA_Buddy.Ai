# Starts QABuddy.ai on Windows without Docker: Qdrant (native binary), the
# embedding model in Ollama, and the web app. Everything runs detached, so it
# keeps running after this window closes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1 -Ingest

param([switch]$Ingest, [switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$storage = Join-Path $root "storage"
New-Item -ItemType Directory -Force $storage | Out-Null

function Test-Url($url) {
    try { Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

if (-not (Test-Path $python)) {
    Write-Host "Creating the virtual environment..."
    py -3.11 -m venv (Join-Path $root ".venv")
    & $python -m pip install -q -r (Join-Path $root "requirements.txt")
}

# 1. Qdrant
if (-not (Test-Url "http://127.0.0.1:6333")) {
    $qdrant = Join-Path $root ".tools\qdrant\qdrant.exe"
    if (-not (Test-Path $qdrant)) {
        Write-Host "Downloading Qdrant..."
        New-Item -ItemType Directory -Force (Split-Path $qdrant) | Out-Null
        $zip = Join-Path (Split-Path $qdrant) "qdrant.zip"
        Invoke-WebRequest -UseBasicParsing "https://github.com/qdrant/qdrant/releases/download/v1.19.2/qdrant-x86_64-pc-windows-msvc.zip" -OutFile $zip
        Expand-Archive $zip -DestinationPath (Split-Path $qdrant) -Force
        Remove-Item $zip
    }
    $env:QDRANT__STORAGE__STORAGE_PATH = Join-Path $storage "qdrant\storage"
    $env:QDRANT__STORAGE__SNAPSHOTS_PATH = Join-Path $storage "qdrant\snapshots"
    $env:QDRANT__TELEMETRY_DISABLED = "true"
    $env:QDRANT__SERVICE__HOST = "127.0.0.1"
    New-Item -ItemType Directory -Force (Join-Path $storage "qdrant") | Out-Null
    Start-Process $qdrant -WorkingDirectory (Join-Path $storage "qdrant") -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $storage "qdrant\qdrant.log") -RedirectStandardError (Join-Path $storage "qdrant\qdrant-error.log")
    for ($i = 0; $i -lt 30 -and -not (Test-Url "http://127.0.0.1:6333"); $i++) { Start-Sleep -Seconds 1 }
}
Write-Host "Qdrant: http://127.0.0.1:6333"

# 2. Embedding model
$model = if ($env:EMBED_MODEL) { $env:EMBED_MODEL } else { "qwen3-embedding:0.6b" }
if (-not ((ollama list) -match [regex]::Escape($model))) { ollama pull $model }
Write-Host "Ollama: $model"

# 3. Index (optional; incremental, only changed files are embedded)
if ($Ingest) { & $python -m qabuddy ingest }

# 4. Web app
if (-not (Test-Url "http://127.0.0.1:8000/api/health")) {
    $env:PYTHONIOENCODING = "utf-8"
    Start-Process $python -ArgumentList "-m", "qabuddy", "serve" -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $storage "web.log") -RedirectStandardError (Join-Path $storage "web-error.log")
    for ($i = 0; $i -lt 30 -and -not (Test-Url "http://127.0.0.1:8000/api/health"); $i++) { Start-Sleep -Seconds 1 }
}
Write-Host "QABuddy.ai: http://127.0.0.1:8000"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:8000" }
