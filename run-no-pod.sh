#!/usr/bin/env bash
# run-no-pod.sh — TalkToCity using individual containers + bridge network
#                 (no Podman pod — fixes port forwarding issues on Windows)
#
# Usage:
#   ./run-no-pod.sh           — build and start everything
#   ./run-no-pod.sh stop      — stop and remove containers
#   ./run-no-pod.sh logs      — tail logs
#   ./run-no-pod.sh ingest    — re-run ingest
#   ./run-no-pod.sh setup     — first-time: rechunk + start + ingest
#   ./run-no-pod.sh rechunk   — rechunk + rebuild backend + ingest
#
# Secrets via env vars:
#   GEMINI_API_KEY=your-key \
#   GOOGLE_CLIENT_ID=your-client-id \
#   JWT_SECRET=your-secret \
#   ./run-no-pod.sh

NETWORK=talktocity-net
DB_VOLUME=talktocity-pgdata

GEMINI_API_KEY="${GEMINI_API_KEY:-}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.0-flash}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
JWT_SECRET="${JWT_SECRET:-dc5cef8ccae02f17044d7ad68c72f7337d937cca641183b74cc1e1bf5a2f5875}"

if [[ -z "$GEMINI_API_KEY" ]]; then
  echo "Error: GEMINI_API_KEY is not set."
  echo "Usage: GEMINI_API_KEY=your-key ./run-no-pod.sh"
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
  echo "Running ingest..."
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

run_rechunk() {
  PYTHON_CMD=""
  if python --version &>/dev/null 2>&1; then
    PYTHON_CMD="python"
  elif python3 --version &>/dev/null 2>&1; then
    PYTHON_CMD="python3"
  else
    echo "Error: Python not found."
    exit 1
  fi
  echo "Rechunking data files..."
  $PYTHON_CMD talktocity/rechunk.py --input talktocity/data/
  echo "Rechunk complete."
}

run_rechunk_and_ingest() {
  if ! podman ps --format "{{.Names}}" 2>/dev/null | grep -q "talktocity-backend"; then
    echo "Error: containers not running. Start first: ./run-no-pod.sh"
    exit 1
  fi
  run_rechunk
  echo "Rebuilding backend image..."
  podman stop talktocity-backend 2>/dev/null || true
  podman rm   talktocity-backend 2>/dev/null || true
  podman rmi  talktocity-backend --force 2>/dev/null || true
  podman build -t talktocity-backend "$(cd talktocity && pwd)"
  podman run -d \
    --name talktocity-backend \
    --network "$NETWORK" \
    -e DATABASE_URL="postgresql+psycopg://postgres:postgres@talktocity-db:5432/talktocity" \
    -e GEMINI_API_KEY="$GEMINI_API_KEY" \
    -e GEMINI_MODEL="$GEMINI_MODEL" \
    -e HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    -e GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
    -e JWT_SECRET="$JWT_SECRET" \
    talktocity-backend
  sleep 3
  run_ingest
  echo "Rechunk + ingest complete."
}

run_setup() {
  echo "=== First-time setup ==="
  run_rechunk
  RUN_INGEST_AFTER_START=1
}

case "${1:-}" in
  stop)    stop_all;               exit 0 ;;
  logs)    logs_all;               exit 0 ;;
  ingest)  run_ingest;             exit 0 ;;
  rechunk) run_rechunk_and_ingest; exit 0 ;;
  setup)   run_setup ;;
esac

# ── 0. Podman Machine check ────────────────────────────────────────────────

echo "Checking Podman Machine..."
MACHINE_STATE=$(podman machine inspect --format "{{.State}}" podman-machine-default 2>/dev/null || echo "unknown")
if [[ "$MACHINE_STATE" == "running" ]]; then
  echo "Podman Machine already running."
else
  echo "Starting Podman Machine..."
  podman machine start 2>&1 || true
fi

# ── 1. Clean up ────────────────────────────────────────────────────────────

stop_all || true

# ── 2. Create network and volume ──────────────────────────────────────────

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

# ── 4. Backend ─────────────────────────────────────────────────────────────

echo "Building backend image..."
podman build -t talktocity-backend "$(cd talktocity && pwd)"

echo "Starting backend..."
podman run -d \
  --name talktocity-backend \
  --network "$NETWORK" \
  -e DATABASE_URL="postgresql+psycopg://postgres:postgres@talktocity-db:5432/talktocity" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e GEMINI_MODEL="$GEMINI_MODEL" \
  -e HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
  -e GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  -e JWT_SECRET="$JWT_SECRET" \
  -p 8000:8000 \
  talktocity-backend

echo "Backend started."

# ── 5. Update nginx.conf to use container name ────────────────────────────
# In bridge network, containers talk via container name not localhost.
# We update nginx.conf dynamically before building the frontend image.

echo "Updating nginx.conf for bridge network..."
sed -i 's|proxy_pass.*http://localhost:8000|proxy_pass         http://talktocity-backend:8000|g' \
  talktocity-react/nginx.conf

# ── 6. Frontend ────────────────────────────────────────────────────────────

echo "Building frontend image..."
podman build -t talktocity-frontend "$(cd talktocity-react && pwd)"

echo "Starting frontend..."
podman run -d \
  --name talktocity-frontend \
  --network "$NETWORK" \
  -p 5173:80 \
  talktocity-frontend

# ── 7. Restore nginx.conf (so it works with pod approach too) ─────────────

sed -i 's|proxy_pass.*http://talktocity-backend:8000|proxy_pass         http://localhost:8000|g' \
  talktocity-react/nginx.conf

# ── 8. Post-start ingest ──────────────────────────────────────────────────

if [[ "${RUN_INGEST_AFTER_START:-0}" == "1" ]]; then
  echo "Running ingest..."
  sleep 3
  run_ingest
  echo "=== Setup complete ==="
fi

# ── Done ───────────────────────────────────────────────────────────────────

echo ""
echo "TalkToCity is running!"
echo ""
echo "  Frontend  ->  http://localhost:5173"
echo "  Backend   ->  http://localhost:8000/health"
echo "  Postgres  ->  localhost:5433"
echo ""
echo "  LLM: Gemini ($GEMINI_MODEL)"
echo ""
echo "  ./run-no-pod.sh stop    - stop everything"
echo "  ./run-no-pod.sh logs    - view logs"
echo "  ./run-no-pod.sh ingest  - re-run ingest"
