#!/usr/bin/env bash
# Build local SQLite RAG index (Ollama embeddings).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"

if ! curl -fsS "${HOST}/api/tags" >/dev/null 2>&1; then
  echo "Ollama is not running."
  echo "  1) Install: https://ollama.com"
  echo "  2) ollama serve"
  echo "  3) ollama pull ${EMBED_MODEL}"
  exit 1
fi

echo "Ensure embed model: ${EMBED_MODEL}"
ollama pull "${EMBED_MODEL}" >/dev/null || true

exec python3 "$ROOT/agents/pr-reviewer/index.py" --embed-model "${EMBED_MODEL}" "$@"
