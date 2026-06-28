@echo off
REM run.cmd — TalkToCity full stack using Docker on Windows CMD
REM
REM Usage:
REM   run.cmd              — build and start everything
REM   run.cmd stop         — stop and remove containers
REM   run.cmd logs         — view logs
REM   run.cmd ingest       — re-run ingest manually
REM
REM With Gemini (set before running):
REM   set GEMINI_API_KEY=your-key
REM   run.cmd
REM
REM With custom model:
REM   set GEMINI_MODEL=gemini-3-flash-preview
REM   run.cmd
REM
REM Prerequisites:
REM   - Docker Desktop installed and running
REM   - GEMINI_API_KEY set before running
REM   - Folders next to this file:
REM       talktocity\          <- Python backend
REM       talktocity-react\    <- React frontend

setlocal EnableDelayedExpansion

set NETWORK=talktocity-net
set DB_VOLUME=talktocity-pgdata
if "%GEMINI_MODEL%"=="" set GEMINI_MODEL=gemini-2.0-flash-lite

REM Require GEMINI_API_KEY
if "%GEMINI_API_KEY%"=="" (
  echo Error: GEMINI_API_KEY is not set.
  echo Usage: set GEMINI_API_KEY=your-key ^& run.cmd
  exit /b 1
)
if "%GEMINI_API_KEY%"=="" set GEMINI_API_KEY=

REM ── Route command ──────────────────────────────────────────────────────────

if "%1"=="stop"   goto :stop_all
if "%1"=="logs"   goto :logs_all
if "%1"=="ingest" goto :run_ingest

REM ── 1. Clean up existing containers ───────────────────────────────────────

echo Cleaning up old containers...
docker stop talktocity-frontend talktocity-backend talktocity-db >nul 2>&1
docker rm   talktocity-frontend talktocity-backend talktocity-db >nul 2>&1
docker network rm %NETWORK% >nul 2>&1

REM ── 2. Create network and volume ──────────────────────────────────────────

echo Creating network and volume...
docker network create %NETWORK% >nul 2>&1
docker volume create %DB_VOLUME% >nul 2>&1

REM ── 3. PostgreSQL + pgvector ───────────────────────────────────────────────

echo Starting Postgres...
docker run -d ^
  --name talktocity-db ^
  --network %NETWORK% ^
  -e POSTGRES_USER=postgres ^
  -e POSTGRES_PASSWORD=postgres ^
  -e POSTGRES_DB=talktocity ^
  -v %DB_VOLUME%:/var/lib/postgresql/data ^
  -p 5433:5432 ^
  pgvector/pgvector:pg16

echo Waiting for Postgres to be ready...
:wait_postgres
docker exec talktocity-db pg_isready -U postgres >nul 2>&1
if errorlevel 1 (
  echo   waiting...
  timeout /t 2 /nobreak >nul
  goto :wait_postgres
)
echo Postgres ready.

docker exec talktocity-db psql -U postgres -d talktocity -c "CREATE EXTENSION IF NOT EXISTS vector;" >nul 2>&1

REM ── 4. Rechunk data ───────────────────────────────────────────────────────

echo Rechunking data files...
if exist talktocity\rechunk.py (
    python talktocity\rechunk.py --input talktocity\data\
    echo Rechunk complete.
)

REM ── 5. Backend ─────────────────────────────────────────────────────────────

echo Building backend image...
docker build -t talktocity-backend talktocity

echo Starting backend...
docker run -d ^
  --name talktocity-backend ^
  --network %NETWORK% ^
  --add-host=host.docker.internal:host-gateway ^
  -e DATABASE_URL="postgresql+psycopg://postgres:postgres@talktocity-db:5432/talktocity" ^
  -e GEMINI_API_KEY=%GEMINI_API_KEY% ^
  -e GEMINI_MODEL=%GEMINI_MODEL% ^
  -p 8000:8000 ^
  talktocity-backend

echo Backend started.

REM ── 6. Ingest ──────────────────────────────────────────────────────────────

echo Running ingest (skips existing chunks)...
timeout /t 3 /nobreak >nul
docker exec talktocity-backend python ingest.py

REM ── 7. Frontend ────────────────────────────────────────────────────────────

echo Building frontend image (takes ~1-2 min first time)...
docker build -t talktocity-frontend talktocity-react

echo Starting frontend...
docker run -d ^
  --name talktocity-frontend ^
  --network %NETWORK% ^
  -p 5173:80 ^
  talktocity-frontend

REM ── Done ───────────────────────────────────────────────────────────────────

echo.
echo ========================================
echo  TalkToCity is running!
echo ========================================
echo.
echo   Frontend  -^>  http://localhost:5173
echo   Backend   -^>  http://localhost:8000/health
echo   Postgres  -^>  localhost:5433
echo.
echo   LLM: Gemini ^(%GEMINI_MODEL%^)
echo.
echo   run.cmd stop    - stop everything
echo   run.cmd logs    - view logs
echo   run.cmd ingest  - re-run ingest
echo.
goto :eof

REM ── Stop ───────────────────────────────────────────────────────────────────

:stop_all
echo Stopping containers...
docker stop talktocity-frontend talktocity-backend talktocity-db >nul 2>&1
docker rm   talktocity-frontend talktocity-backend talktocity-db >nul 2>&1
docker network rm %NETWORK% >nul 2>&1
echo Done.
goto :eof

REM ── Logs ───────────────────────────────────────────────────────────────────

:logs_all
echo === DB ===
docker logs talktocity-db 2>&1 | more
echo === Backend ===
docker logs talktocity-backend 2>&1 | more
echo === Frontend ===
docker logs talktocity-frontend 2>&1 | more
goto :eof

REM ── Ingest ─────────────────────────────────────────────────────────────────

:run_ingest
echo Running ingest...
docker exec talktocity-backend python ingest.py
goto :eof
