# Local Orchestrator (multi-agent fan-out)

Coordinates the **PR**, **security**, and **performance** reviewers (and optionally the **bug investigator**) on the same change set, then aggregates findings into one dashboard. Uses the shared Ollama + SQLite RAG stack — no Cursor Cloud.

## Setup

Same as the other agents:

```bash
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
./scripts/pr-review-index.sh   # shared index
```

## What it does

```
./scripts/orchestrate.sh
        │
        ├── pr-reviewer
        ├── security-reviewer
        └── performance-reviewer
                │
                ▼
   agents/orchestrator/reports/  (combined HTML + JSON)
```

Bug investigator is included when you pass `--bug` / `--stacktrace`, or list it in `--agents`.

## Run

```bash
# default: pr + security + performance on current branch
./scripts/orchestrate.sh

# GitHub PR (read-only fetch)
./scripts/orchestrate.sh --pr 42

# parallel specialists
./scripts/orchestrate.sh --pr 42 --parallel

# subset
./scripts/orchestrate.sh --agents security,performance

# include bug investigation
./scripts/orchestrate.sh --bug "Confirm booking does nothing" --pr 42

# stop scheduling after first failure (sequential only)
./scripts/orchestrate.sh --fail-fast
```

Reports land in `agents/orchestrator/reports/` (local only — gitignored). Child reports remain under each specialist’s `reports/latest/`.

### Useful options

| Flag / env | Meaning |
|---|---|
| `--pr 42` | Same PR scope for every child |
| `--base origin/main` | Diff base (`ORCHESTRATE_BASE` / `PR_REVIEW_BASE`) |
| `--agents pr,security` | Which specialists to run |
| `--parallel` | Run children concurrently |
| `--fail-fast` | Skip remaining agents after a failure (sequential) |
| `--bug` / `--stacktrace` | Forward to bug investigator (auto-adds `bug`) |
| `--model` / `--strong-model` | Forwarded to children |
| `--no-rag` / `--dry-gather` | Forwarded to children |
| `--no-report` / `--no-open` | Skip combined HTML pack / browser open |
| `--json-out path` | Write aggregated JSON |

## Shared pieces

- RAG index: `agents/pr-reviewer/index/rag.sqlite`
- Rules: `RULES.md`
- Schema: `schema/orchestration_output.schema.json`

The orchestrator does **not** invent findings — it only routes and aggregates specialist output. Never auto-patches or merges.
