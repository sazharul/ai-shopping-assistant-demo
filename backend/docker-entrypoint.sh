#!/bin/sh
set -e

if [ ! -f .env ]; then
  cp .env.example .env
fi

export DEMO_MODE=true

if [ ! -d data/faiss_index ] || [ ! -d data/product_faiss_index ]; then
  echo "[entrypoint] Building demo FAISS indexes (requires OPENAI_API_KEY)..."
  python scripts/build_demo_indexes.py || echo "[entrypoint] Index build skipped — set OPENAI_API_KEY and run manually"
fi

exec uvicorn main:app --host 0.0.0.0 --port 8001
