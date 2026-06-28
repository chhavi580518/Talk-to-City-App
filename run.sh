#!/usr/bin/env bash
# run.sh — TalkToCity full stack on Podman Desktop (Windows)
# Uses individual containers + bridge network (no pod) for reliable port forwarding.
#
# Usage:
#   ./run.sh            — build and start everything
#   ./run.sh setup      — first-time: rechunk + start + ingest
#   ./run.sh stop       — stop and remove containers
#   ./run.sh logs       — tail logs from all containers
#   ./run.sh ingest     — re-run ingest (current EMBEDDING_MODEL)
#   ./run.sh ingest-all  — populate both minilm + labse collections
#   ./run.sh setup-all   — rechunk + rebuild + ingest both collections
#   ./run.sh rechunk    — rechunk + rebuild backend + ingest
#
# Secrets via env vars (never hardcode):
#   GEMINI_API_KEY=your-key \
#   GOOGLE_CLIENT_ID=your-client-id \
#   JWT_SECRET=your-secret \
#   EMBEDDING_MODEL=minilm \     # or labse
#   ./run.sh
#
# Or use a .env file:
#   source .env && ./run.sh

NETWORK=talktocity-net
DB_VOLUME=talktocity-pgdata

GEMINI_API_KEY="${GEMINI_API_KEY:-}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.0-flash}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-labse}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
JWT_SECRET="${JWT_SECRET:-change-this-secret}"

if [[ -z "$GEMINI_API_KEY" ]]; then
  echo "Error: GEMINI_API_KEY is not set."
  echo "Usage: GEMINI_API_KEY=your-key ./run.sh"
  exit 1
fi

# ── Helpers ────────────────────────────────────────────────────────────────

stop_all() {
  echo "Stopping containers..."
  podman stop  talktocity-frontend talktocity-backend talktocity-db 2>/dev/null || true
  podman rm -f talktocity-frontend talktocity-backend talktocity-db 2>/dev/null || true
  podman network rm "$NETWORK" 2>/dev/null || true
  echo "Stopped."
}

logs_all() {
  echo "=== DB ==="       && podman logs talktocity-db       2>&1 | tail -30
  echo "=== Backend ===" && podman logs talktocity-backend  2>&1 | tail -30
  echo "=== Frontend ===" && podman logs talktocity-frontend 2>&1 | tail -30
}

run_ingest() {
  echo "Running ingest (model=$EMBEDDING_MODEL)..."
  podman exec talktocity-backend python ingest.py
}

wait_for_postgres() {
  echo "Waiting for Postgres..."
  for i in $(seq 1 20); do
    if podman exec talktocity-db pg_isready -U postgres &>/dev/null; then
      echo "Postgres ready."
      return 0
    fi
    echo "  ($i/20)..."
    sleep 2
  done
  echo "Error: Postgres did not become ready."
  exit 1
}

wait_for_backend() {
  local model=$1
  local max=${2:-40}
  echo "Waiting for backend ($model) to be ready..."
  echo "(Streaming logs — model download progress will appear below)"
  echo "──────────────────────────────────────────────────────────"

  for i in $(seq 1 $max); do
    # Print latest log line so user can see download/load progress
    local last_log
    last_log=$(podman logs talktocity-backend 2>&1 | tail -1)
    if [[ -n "$last_log" ]]; then
      echo "  [$i/$max] $last_log"
    else
      echo "  [$i/$max] starting..."
    fi

    # Check if uvicorn is actually serving
    if podman exec talktocity-backend         python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"         &>/dev/null 2>&1; then
      echo "──────────────────────────────────────────────────────────"
      echo "Backend ready."
      return 0
    fi
    sleep 5
  done
  echo "──────────────────────────────────────────────────────────"
  echo "Warning: backend health check timed out — proceeding anyway."
}

start_backend() {
  local model=$1
  podman stop talktocity-backend 2>/dev/null || true
  podman rm   talktocity-backend 2>/dev/null || true
  podman run -d \
    --name talktocity-backend \
    --network "$NETWORK" \
	--memory 8g \
	--memory-swap 8g \
    -e DATABASE_URL="postgresql+psycopg://postgres:postgres@talktocity-db:5432/talktocity" \
    -e GEMINI_API_KEY="$GEMINI_API_KEY" \
    -e GEMINI_MODEL="$GEMINI_MODEL" \
    -e HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    -e EMBEDDING_MODEL="$model" \
    -e GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
    -e JWT_SECRET="$JWT_SECRET" \
    -p 8000:8000 \
    talktocity-backend
}

run_rechunk() {
  PYTHON_CMD=""
  if python --version &>/dev/null 2>&1; then
    PYTHON_CMD="python"
  elif python3 --version &>/dev/null 2>&1; then
    PYTHON_CMD="python3"
  else
    echo "Error: Python not found. Run rechunk inside container instead:"
    echo "  podman exec talktocity-backend python rechunk.py --input data/"
    exit 1
  fi
  echo "Rechunking data files..."
  $PYTHON_CMD talktocity/rechunk.py --input talktocity/data/
  echo "Rechunk complete."
}

run_rechunk_and_ingest() {
  if ! podman ps --format "{{.Names}}" 2>/dev/null | grep -q "talktocity-backend"; then
    echo "Error: containers not running. Start first: ./run.sh"
    exit 1
  fi
  run_rechunk
  echo "Rebuilding backend image with new chunks..."
  podman rmi talktocity-backend --force 2>/dev/null || true
  podman build -t talktocity-backend "$(cd talktocity && pwd)"
  start_backend "$EMBEDDING_MODEL"
  wait_for_backend "$EMBEDDING_MODEL" 20
  run_ingest
  echo "Rechunk + ingest complete."
}

run_ingest_all() {
  echo "=== Ingesting both MiniLM and LaBSE collections ==="

  echo ""
  # LaBSE first — creates the embedding column without a fixed dimension
  # constraint. If MiniLM runs first, the 384-dim column rejects LaBSE's 768-dim vectors.
  echo "Step 1/3: LaBSE (768-dim) → talktocity_chunks_labse"
  start_backend "labse"
  wait_for_backend "labse" 120   # LaBSE: up to 10 min on first download (1.8GB)
  podman exec talktocity-backend python ingest.py

  echo ""
  echo "Step 2/3: MiniLM (384-dim) → talktocity_chunks_minilm"
  start_backend "minilm"
  wait_for_backend "minilm" 24   # MiniLM: ~2 min
  podman exec talktocity-backend python ingest.py

  echo ""
  echo "Step 3/3: Restarting backend with EMBEDDING_MODEL=$EMBEDDING_MODEL"
  start_backend "$EMBEDDING_MODEL"

  echo ""
  echo "=== Both collections ready ==="
  podman exec talktocity-db psql -U postgres -d talktocity \
    -c "SELECT c.name, COUNT(e.id) as chunks FROM langchain_pg_collection c LEFT JOIN langchain_pg_embedding e ON e.collection_id = c.uuid GROUP BY c.name ORDER BY c.name;" 2>/dev/null || true

  # Reload nginx to resolve new backend IP
  sleep 3
  podman exec talktocity-frontend nginx -s reload 2>/dev/null || true
}

run_setup_all() {
  echo "=== Full setup: rechunk + ingest both collections ==="

  echo ""
  echo "Step 1/4: Rechunking data..."
  run_rechunk

  echo ""
  echo "Step 2/4: Rebuilding backend image with new chunks..."
  podman rmi talktocity-backend --force 2>/dev/null || true
  podman build -t talktocity-backend "$(cd talktocity && pwd)"

  echo ""
  echo "Step 3/4: Starting DB + frontend..."
  # DB should already be running — start frontend if not
  if ! podman ps --format "{{.Names}}" 2>/dev/null | grep -q "talktocity-db"; then
    echo "Error: DB not running. Start the full stack first: ./run.sh"
    echo "Then run: ./run.sh setup-all"
    exit 1
  fi

  echo ""
  echo "Step 4/4: Ingesting both collections..."
  run_ingest_all

  echo ""
  echo "=== Full setup complete ==="
}

run_setup() {
  echo "=== First-time setup ==="
  echo "Step 1: Rechunking data..."
  run_rechunk
  echo "Step 2: Starting the full stack..."
  RUN_INGEST_AFTER_START=1
}

case "${1:-}" in
  stop)       stop_all;               exit 0 ;;
  logs)       logs_all;               exit 0 ;;
  ingest)     run_ingest;             exit 0 ;;
  ingest-all) run_ingest_all;         exit 0 ;;
  setup-all)  run_setup_all;          exit 0 ;;
  rechunk)    run_rechunk_and_ingest; exit 0 ;;
  setup)      run_setup ;;
esac

# ── 0. Podman Machine check ────────────────────────────────────────────────

echo "Checking Podman Machine..."
MACHINE_STATE=$(podman machine inspect --format "{{.State}}" podman-machine-default 2>/dev/null || echo "unknown")
if [[ "$MACHINE_STATE" == "running" ]]; then
  echo "Podman Machine already running."
else
  echo "Podman Machine state: $MACHINE_STATE — starting..."
  podman machine start 2>&1 || true
  echo "Podman Machine ready."
fi

# ── 1. Clean up ────────────────────────────────────────────────────────────

stop_all || true

# ── 2. Network and volume ─────────────────────────────────────────────────

podman network create "$NETWORK" 2>/dev/null || true
podman volume  create "$DB_VOLUME" 2>/dev/null || true

# ── 3. PostgreSQL + pgvector ───────────────────────────────────────────────

echo "Starting Postgres..."
podman run -d \
  --name talktocity-db \
  --network "$NETWORK" \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=talktocity \
  -v "$DB_VOLUME":/var/lib/postgresql/data \
  -p 5433:5432 \
  pgvector/pgvector:pg16

wait_for_postgres

podman exec talktocity-db \
  psql -U postgres -d talktocity \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true

echo "Giving Postgres a moment to fully initialize..."
sleep 3

# ── 4. Backend ─────────────────────────────────────────────────────────────

echo "Building backend image..."
podman build -t talktocity-backend "$(cd talktocity && pwd)"

start_backend "$EMBEDDING_MODEL"
echo "Backend started (EMBEDDING_MODEL=$EMBEDDING_MODEL)."

# ── 5. Frontend ────────────────────────────────────────────────────────────

echo "Building frontend image..."
podman build -t talktocity-frontend "$(cd talktocity-react && pwd)"

podman run -d \
  --name talktocity-frontend \
  --network "$NETWORK" \
  --memory 2g \
  -p 5173:80 \
  talktocity-frontend

echo "Frontend started."

# ── 6. Post-start ingest (setup only) ─────────────────────────────────────

if [[ "${RUN_INGEST_AFTER_START:-0}" == "1" ]]; then
  echo ""
  echo "Step 3: Running ingest..."
  sleep 3
  run_ingest
  echo "=== Setup complete ==="
fi

# ── 7. Reload nginx to pick up fresh backend IP ───────────────────────────

echo "Reloading nginx to resolve backend IP..."
sleep 2
podman exec talktocity-frontend nginx -s reload 2>/dev/null || true

# ── Done ───────────────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo " TalkToCity is running!"
echo "========================================"
echo ""
echo "  Frontend   ->  http://localhost:5173"
echo "  Backend    ->  http://localhost:8000/health"
echo "  Postgres   ->  localhost:5433"
echo ""
echo "  LLM:        Gemini ($GEMINI_MODEL)"
echo "  Embeddings: $EMBEDDING_MODEL"
echo ""
echo "  ./run.sh stop       - stop everything"
echo "  ./run.sh logs       - view logs"
echo "  ./run.sh ingest     - re-run ingest"
echo "  ./run.sh ingest-all - populate both minilm + labse collections"
echo "  ./run.sh setup      - first-time setup"
echo "  ./run.sh rechunk    - rechunk + rebuild + ingest"
