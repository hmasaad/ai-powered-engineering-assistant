#!/usr/bin/env bash
# Local PR review via Ollama + SQLite RAG (no Cursor Cloud).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL="${OLLAMA_MODEL:-llama3.2}"
BASE="${PR_REVIEW_BASE:-origin/main}"
HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
INDEX="$ROOT/agents/pr-reviewer/index/rag.sqlite"

if ! curl -fsS "${HOST}/api/tags" >/dev/null 2>&1; then
  echo "Ollama is not running."
  echo "  1) Install: https://ollama.com"
  echo "  2) ollama serve"
  echo "  3) ollama pull ${MODEL}"
  echo "  4) ollama pull nomic-embed-text"
  exit 1
fi

if [[ ! -f "$INDEX" ]]; then
  echo "RAG index missing. Building it now..."
  "$ROOT/scripts/pr-review-index.sh"
fi

exec python3 "$ROOT/agents/pr-reviewer/review.py" --base "$BASE" --model "$MODEL" "$@"
