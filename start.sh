#!/bin/bash
set -e

echo "========================================="
echo "Ralph Loop - Starting deployment"
echo "========================================="

echo "[1/3] Running database migrations..."
alembic upgrade head

echo "[2/3] Migrations complete!"
echo "[3/3] Starting FastAPI application..."

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
