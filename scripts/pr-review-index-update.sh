#!/usr/bin/env bash
# Incremental RAG index update for changed files (merge / push to main).
# Usage:
#   ./scripts/pr-review-index-update.sh --since <before_sha> [--until HEAD]
#   ./scripts/pr-review-index-update.sh --files lib/foo.dart --deleted lib/old.dart
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"

if ! curl -fsS "${HOST}/api/tags" >/dev/null 2>&1; then
  echo "Ollama is not running at ${HOST}."
  exit 1
fi

ollama pull "${EMBED_MODEL}" >/dev/null || true

exec python3 "$ROOT/agents/pr-reviewer/index.py" --embed-model "${EMBED_MODEL}" "$@"
