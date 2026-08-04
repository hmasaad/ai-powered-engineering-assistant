# Local PR Reviewer (Ollama + SQLite RAG)

Reviews git changes on your machine using project rules and retrieved code context. Does **not** use Cursor Cloud.

## Setup

```bash
# Install Ollama: https://ollama.com
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
```

## Build / refresh the RAG index

```bash
./scripts/pr-review-index.sh
```

Creates `agents/pr-reviewer/index/rag.sqlite` (gitignored).

## Run a review

```bash
# current branch vs origin/main
./scripts/pr-review.sh

# specific GitHub PR number
./scripts/pr-review.sh --pr 42
```

`--pr` fetches `pull/<n>/head` from `origin` (does not change your checkout).  
If `gh` is installed, it also detects the PR base branch and title.

If the index is missing, the review script builds it first.

### Useful options

| Flag / env | Meaning |
|---|---|
| `--pr 42` | Review GitHub PR #42 |
| `--base origin/main` | Diff base (`PR_REVIEW_BASE`; used when not using `gh` with `--pr`) |
| `--model llama3.2` | Chat model (`OLLAMA_MODEL`) |
| `--embed-model nomic-embed-text` | Embed model (`OLLAMA_EMBED_MODEL`) |
| `--top-k 8` | Retrieved chunks (`PR_REVIEW_TOP_K`) |
| `--no-rag` | Skip retrieval |
| `--dry-gather` | Print prompt context only |

## Pipeline

1. Read `RULES.md`
2. Collect git diff + dirty files + `flutter analyze`
3. Embed a query from changed paths/symbols
4. Retrieve top chunks from SQLite (cosine similarity)
5. Ask Ollama for Blockers / Should fix / Nits

## Edit behavior

Change review standards in `RULES.md`, then re-index if you want rules text in the vector store (rules are always injected directly into the prompt too).
