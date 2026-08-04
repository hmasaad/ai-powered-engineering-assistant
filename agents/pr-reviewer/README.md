# Local PR Reviewer (Ollama + SQLite RAG)

Reviews git changes on your machine using project rules and retrieved code context. Does **not** use Cursor Cloud.

## Setup

```bash
# Install Ollama: https://ollama.com
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
```

## RAG index

### Full rebuild

```bash
./scripts/pr-review-index.sh
```

Writes `agents/pr-reviewer/index/rag.sqlite` (tracked in git).

### Incremental update (same logic as CI)

```bash
# after merge / for a commit range
./scripts/pr-review-index-update.sh --since <before_sha> --until HEAD

# or explicit paths
./scripts/pr-review-index-update.sh --files lib/ui/home/salon_list_screen.dart
```

Flow:

1. Identify modified / deleted indexable files  
2. Remove old embeddings for those paths  
3. Embed updated content  
4. Write SQLite  

### On merge to main (GitHub Action)

Workflow: `.github/workflows/rag-index-on-merge.yml`

- Trigger: `push` to `main` (PR merge) or manual `workflow_dispatch`  
- Installs Ollama + `nomic-embed-text`  
- Runs incremental update for `before...sha` (full rebuild if needed)  
- Commits updated `rag.sqlite` to `main`  

After that, `git pull` on your machine picks up the latest index for reviews.

## Run a review

```bash
./scripts/pr-review.sh
./scripts/pr-review.sh --pr 42
```

If the index is missing, the review script builds it first.

### Useful options

| Flag / env | Meaning |
|---|---|
| `--pr 42` | Review GitHub PR #42 |
| `--base origin/main` | Diff base (`PR_REVIEW_BASE`) |
| `--model llama3.2` | Chat model (`OLLAMA_MODEL`) |
| `--embed-model nomic-embed-text` | Embed model (`OLLAMA_EMBED_MODEL`) |
| `--top-k 8` | Retrieved chunks (`PR_REVIEW_TOP_K`) |
| `--no-rag` | Skip retrieval |
| `--no-open` | Don’t open HTML report |
| `--dry-gather` | Print prompt context only |

## Pipeline

1. Read `RULES.md`  
2. Collect git diff + `flutter analyze`  
3. Retrieve related chunks from SQLite  
4. Ask Ollama (JSON findings + triage)  
5. Write HTML report under `agents/pr-reviewer/reports/`  

## Edit behavior

Change review standards in `RULES.md`. Re-index after large refactors (`./scripts/pr-review-index.sh`) or rely on the merge Action for routine updates.
