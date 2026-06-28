#!/usr/bin/env bash
# run.sh — TalkToCity full stack on Linux (Docker or Podman)
#
# Usage:
#   ./run.sh                                    — build and start everything
#   ./run.sh stop                               — stop and remove containers
#   ./run.sh logs                               — tail logs from all containers
#   ./run.sh ingest                             — re-run ingest manually
#   ./run.sh rebuild                            — force rebuild all images
#
# Usage with Gemini:
#   GEMINI_API_KEY=your-key ./run-linux.sh
#   GEMINI_API_KEY=your-key GEMINI_MODEL=gemini-3-flash-preview ./run-linux.sh
#
# Prerequisites:
#   - Docker or Podman installed
#   - GEMINI_API_KEY env var set
#   - Project folders next to this script:
#       talktocity/          <- Python backend
#       talktocity-react/    <- React frontend

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────

NETWORK=talktocity-net
DB_VOLUME=talktocity-pgdata

GEMINI_API_KEY="${GEMINI_API_KEY:-}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.0-flash-lite}"

if [[ -z "$GEMINI_API_KEY" ]]; then
  echo "Error: GEMINI_API_KEY is not set."
  echo "Usage: GEMINI_API_KEY=your-key ./run-linux.sh"
  exit 1
fi

# ── Detect container runtime ───────────────────────────────────────────────

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  RUNTIME=docker
elif command -v podman &>/dev/null; then
  RUNTIME=podman
else
  echo "Error: neither Docker nor Podman found. Please install one first."
  exit 1
fi

echo "Using runtime: $RUNTIME"

# ── Helpers ────────────────────────────────────────────────────────────────

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✓ $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ✗ $*" >&2; }

stop_all() {
  log "Stopping containers..."
  $RUNTIME stop  talktocity-frontend talktocity-backend talktocity-db 2>/dev/null || true
  $RUNTIME rm -f talktocity-frontend talktocity-backend talktocity-db 2>/dev/null || true
  $RUNTIME network rm "$NETWORK" 2>/dev/null || true
  ok "Stopped."
}

logs_all() {
  echo "=== DB ==="       && $RUNTIME logs talktocity-db       2>&1 | tail -40
  echo "=== Backend ===" && $RUNTIME logs talktocity-backend  2>&1 | tail -40
  echo "=== Frontend ===" && $RUNTIME logs talktocity-frontend 2>&1 | tail -40
}

run_ingest() {
  log "Running ingest..."
  $RUNTIME exec talktocity-backend python ingest.py
}

wait_for_postgres() {
  log "Waiting for Postgres to be ready..."
  for i in $(seq 1 30); do
    if $RUNTIME exec talktocity-db pg_isready -U postgres &>/dev/null; then
      ok "Postgres ready."
      return 0
    fi
    echo "  ($i/30) waiting..."
    sleep 2
  done
  err "Postgres did not become ready in time."
  exit 1
}

# ── Argument routing ───────────────────────────────────────────────────────

case "${1:-}" in
  stop)    stop_all;   exit 0 ;;
  logs)    logs_all;   exit 0 ;;
  ingest)  run_ingest; exit 0 ;;
  rebuild)
    log "Force removing images..."
    $RUNTIME rmi talktocity-backend  --force 2>/dev/null || true
    $RUNTIME rmi talktocity-frontend --force 2>/dev/null || true
    ;;
esac

# ── 1. Clean up existing containers ───────────────────────────────────────

stop_all || true

# ── 2. Create network and volume ──────────────────────────────────────────

$RUNTIME network create "$NETWORK" 2>/dev/null || true
$RUNTIME volume  create "$DB_VOLUME" 2>/dev/null || true

# ── 3. PostgreSQL + pgvector ───────────────────────────────────────────────

log "Starting Postgres..."
$RUNTIME run -d \
  --name talktocity-db \
  --network "$NETWORK" \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=talktocity \
  -v "${DB_VOLUME}:/var/lib/postgresql/data" \
  -p 5433:5432 \
  pgvector/pgvector:pg16

wait_for_postgres

$RUNTIME exec talktocity-db \
  psql -U postgres -d talktocity \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true

ok "pgvector extension ready."

# ── 4. Rechunk data ───────────────────────────────────────────────────────

if [ -f "talktocity/rechunk.py" ]; then
  log "Rechunking data files..."
  python3 talktocity/rechunk.py --input talktocity/data/ || true
  ok "Rechunk complete."
fi

# ── 5. Backend ─────────────────────────────────────────────────────────────

log "Building backend image..."
$RUNTIME build -t talktocity-backend ./talktocity

log "Starting backend..."
$RUNTIME run -d \
  --name talktocity-backend \
  --network "$NETWORK" \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="postgresql+psycopg://postgres:postgres@talktocity-db:5432/talktocity" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e GEMINI_MODEL="$GEMINI_MODEL" \
  -p 8000:8000 \
  talktocity-backend

ok "Backend started."

# ── 6. Ingest ──────────────────────────────────────────────────────────────

log "Running ingest (skips existing chunks)..."
sleep 3
$RUNTIME exec talktocity-backend python ingest.py || true

# ── 7. Frontend ────────────────────────────────────────────────────────────

log "Building frontend image (takes ~1-2 min first time)..."
$RUNTIME build -t talktocity-frontend ./talktocity-react

log "Starting frontend..."
$RUNTIME run -d \
  --name talktocity-frontend \
  --network "$NETWORK" \
  -p 5173:80 \
  talktocity-frontend

ok "Frontend started."

# ── Done ───────────────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo " TalkToCity is running!"
echo "========================================"
echo ""
echo "  Frontend  ->  http://localhost:5173"
echo "  Backend   ->  http://localhost:8000/health"
echo "  Postgres  ->  localhost:5433"
echo ""
echo "  LLM: Gemini ($GEMINI_MODEL)"
echo ""
echo "  ./run.sh stop     — stop everything"
echo "  ./run.sh logs     — view logs"
echo "  ./run.sh ingest   — re-run ingest"
echo "  ./run.sh rebuild  — force rebuild images"
echo ""
