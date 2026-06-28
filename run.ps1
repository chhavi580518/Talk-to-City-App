# run.ps1 — TalkToCity full stack on Podman Desktop (Windows)
#
# Run from PowerShell (not CMD).
#
# Usage:
#   .\run.ps1            — build and start everything
#   .\run.ps1 stop       — stop and remove the pod
#   .\run.ps1 logs       — tail logs from all containers
#   .\run.ps1 ingest     — re-run ingest manually
#
# Prerequisites:
#   - Podman Desktop installed and running (whale icon in system tray)
#   - GEMINI_API_KEY set: $env:GEMINI_API_KEY = 'your-key'
#   - Project folders laid out like this:
#       run.ps1
#       talktocity\          <- Python backend
#       talktocity-react\    <- React frontend
#
# First time? You may need to allow script execution:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

param(
    [string]$Command = ""
)

$ErrorActionPreference = "Stop"

$POD        = "talktocity"
$DB_VOLUME  = "talktocity-pgdata"

# ── Detect Windows host IP ─────────────────────────────────────────────────
# Podman Desktop runs containers inside a VM.
# We need the Windows host IP on the WSL/Hyper-V adapter so containers
# can reach Ollama running on the host.

function Get-HostIP {
    # Allow manual override: $env:HOST_IP = "192.168.x.x"
    if ($env:HOST_IP) { return $env:HOST_IP }

    # Find the WSL / vEthernet adapter IP
    $adapters = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.InterfaceAlias -match "WSL|vEthernet|Hyper-V"
        } |
        Select-Object -ExpandProperty IPAddress

    if ($adapters) { return ($adapters | Select-Object -First 1) }

    # Fallback: first non-loopback non-APIPA address
    $fallback = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notmatch "^127\." -and
            $_.IPAddress -notmatch "^169\.254\."
        } |
        Select-Object -ExpandProperty IPAddress -First 1

    return $fallback
}

# ── Helper functions ───────────────────────────────────────────────────────

function Stop-Pod {
    Write-Host "Stopping pod '$POD'..." -ForegroundColor Yellow
    podman pod stop $POD 2>$null
    podman pod rm -f $POD 2>$null
    Write-Host "Stopped." -ForegroundColor Green
}

function Show-Logs {
    Write-Host "=== DB ===" -ForegroundColor Cyan
    podman logs talktocity-db 2>&1 | Select-Object -Last 30
    Write-Host "=== Backend ===" -ForegroundColor Cyan
    podman logs talktocity-backend 2>&1 | Select-Object -Last 30
    Write-Host "=== Frontend ===" -ForegroundColor Cyan
    podman logs talktocity-frontend 2>&1 | Select-Object -Last 30
}

function Run-Ingest {
    Write-Host "Running ingest..." -ForegroundColor Yellow
    podman exec talktocity-backend python ingest.py
}

function Wait-ForPostgres {
    Write-Host "Waiting for Postgres to be ready..." -ForegroundColor Yellow
    for ($i = 1; $i -le 20; $i++) {
        $result = podman exec talktocity-db pg_isready -U postgres 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Postgres is ready." -ForegroundColor Green
            return
        }
        Write-Host "  ($i/20) waiting..."
        Start-Sleep -Seconds 2
    }
    Write-Host "Postgres did not become ready in time." -ForegroundColor Red
    exit 1
}

# ── Argument routing ───────────────────────────────────────────────────────

switch ($Command.ToLower()) {
    "stop"   { Stop-Pod;    exit 0 }
    "logs"   { Show-Logs;   exit 0 }
    "ingest" { Run-Ingest;  exit 0 }
}

# ── 0. Detect host IP ──────────────────────────────────────────────────────

$HOST_IP = Get-HostIP

if (-not $HOST_IP) {
    Write-Host ""
    Write-Host "Could not auto-detect your Windows host IP." -ForegroundColor Red
    Write-Host "Find it: run  ipconfig  and look for the WSL or vEthernet adapter."
    Write-Host "Then re-run:  `$env:HOST_IP = '172.x.x.x'; .\run.ps1"
    Write-Host ""
    exit 1
}

$OLLAMA_URL = "http://${HOST_IP}:11434/api/generate"
Write-Host "Windows host IP : $HOST_IP" -ForegroundColor Cyan
Write-Host "Ollama URL       : $OLLAMA_URL" -ForegroundColor Cyan

# ── 1. Ensure Podman Machine is running ────────────────────────────────────

Write-Host "`nChecking Podman Machine..." -ForegroundColor Yellow
$machineStatus = podman machine list 2>&1
if ($machineStatus -notmatch "Running") {
    Write-Host "Starting Podman Machine..." -ForegroundColor Yellow
    podman machine start
}
Write-Host "Podman Machine is running." -ForegroundColor Green

# ── 2. Clean up existing pod ───────────────────────────────────────────────

try { Stop-Pod } catch {}

# ── 3. Create persistent volume ────────────────────────────────────────────

Write-Host "`nCreating volume '$DB_VOLUME'..." -ForegroundColor Yellow
podman volume create $DB_VOLUME 2>$null
# Ignore error if it already exists

# ── 4. Create pod ──────────────────────────────────────────────────────────

Write-Host "Creating pod '$POD'..." -ForegroundColor Yellow
podman pod create `
    --name $POD `
    -p 5173:80 `
    -p 8000:8000 `
    -p 5433:5432

Write-Host "Pod created." -ForegroundColor Green

# ── 5. PostgreSQL + pgvector ───────────────────────────────────────────────

Write-Host "`nStarting Postgres..." -ForegroundColor Yellow
podman run -d `
    --pod $POD `
    --name talktocity-db `
    -e POSTGRES_USER=postgres `
    -e POSTGRES_PASSWORD=postgres `
    -e POSTGRES_DB=talktocity `
    -v "${DB_VOLUME}:/var/lib/postgresql/data" `
    pgvector/pgvector:pg16

Wait-ForPostgres

podman exec talktocity-db `
    psql -U postgres -d talktocity `
    -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>$null

Write-Host "pgvector extension ready." -ForegroundColor Green

# ── 6. Backend (FastAPI) ───────────────────────────────────────────────────

Write-Host "`nBuilding backend image..." -ForegroundColor Yellow
$backendPath = (Resolve-Path ".\talktocity").Path
podman build -t talktocity-backend $backendPath

podman run -d `
    --pod $POD `
    --name talktocity-backend `
    -e DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/talktocity" `
    -e OLLAMA_URL=$OLLAMA_URL `
    talktocity-backend

Write-Host "Backend started." -ForegroundColor Green

# ── 7. Ingest data ─────────────────────────────────────────────────────────

Write-Host "`nRunning ingest (skips existing chunks)..." -ForegroundColor Yellow
Start-Sleep -Seconds 3
try {
    podman exec talktocity-backend python ingest.py
} catch {
    Write-Host "Ingest encountered an error (may already be ingested): $_" -ForegroundColor Yellow
}

# ── 8. Frontend (React + Nginx) ────────────────────────────────────────────

Write-Host "`nBuilding frontend image (takes ~1-2 min first time)..." -ForegroundColor Yellow
$frontendPath = (Resolve-Path ".\talktocity-react").Path
podman build -t talktocity-frontend $frontendPath

podman run -d `
    --pod $POD `
    --name talktocity-frontend `
    talktocity-frontend

Write-Host "Frontend started." -ForegroundColor Green

# ── Done ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " TalkToCity is running!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Frontend  ->  http://localhost:5173"
Write-Host "  Backend   ->  http://localhost:8000/health"
Write-Host "  Postgres  ->  localhost:5433"
Write-Host ""
Write-Host "  LLM: Gemini ($GEMINI_MODEL)"
Write-Host ""
Write-Host "Commands:"
Write-Host "  .\run.ps1 stop    -> stop everything"
Write-Host "  .\run.ps1 logs    -> view container logs"
Write-Host "  .\run.ps1 ingest  -> re-run ingest"
Write-Host ""
