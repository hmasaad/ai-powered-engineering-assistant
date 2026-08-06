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

## Guardrails (v1)

Enforced in code (`guardrails.py`) before/after the model:

| Guardrail | Behavior |
|-----------|----------|
| Changed files only | Diff + nearby context limited to reviewable changed paths |
| Ignore junk | Skips generated, binary, lockfiles, deps, RAG artifacts |
| Secret scan | Detects & redacts keys/tokens before prompting |
| Nearby context | Hunk ± ~25 lines — not whole-file dumps |
| Structured findings | Requires file, line, severity, explanation, recommendation, confidence, evidence |
| Evidence-bound | Drops findings without grounded evidence |
| Confidence filter | Drops findings below `--min-confidence` (default 0.55) |
| JSON schema | Validates model output; rejects invalid payloads |
| Mutes | `mutes.yaml` suppresses recurring noise |
| Read-only | Never approves, merges, or modifies PRs |

## High-impact pipeline

1. **Deterministic prechecks** — flutter analyze + layer/DI/debug heuristics (before LLM)  
2. **Triage model** — fast pass for residual risks  
3. **Strong model routing** — re-checks `blocker`/`should_fix` when `--strong-model` differs  
4. **Evidence + confidence + mutes** — post-filters  
5. **Report pack** — `reports/pr-N/report.json` + `index.html` + dashboard  

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
| `--model llama3.2` | Triage model (`OLLAMA_TRIAGE_MODEL` / `OLLAMA_MODEL`) |
| `--strong-model llama3.2` | Strong pass model (`OLLAMA_STRONG_MODEL`) |
| `--no-routing` | Skip triage→strong routing |
| `--embed-model nomic-embed-text` | Embed model (`OLLAMA_EMBED_MODEL`) |
| `--top-k 8` | Retrieved chunks (`PR_REVIEW_TOP_K`) |
| `--min-confidence 0.55` | Drop low-confidence findings |
| `--json-out path.json` | Write merged JSON review payload |
| `--no-rag` | Skip retrieval |
| `--no-report` | Skip HTML/JSON report pack |
| `--no-open` | Don’t open HTML report |
| `--dry-gather` | Print redacted prompt context only |

## Advanced RAG

Retrieval is **hybrid**, not vector-only:

1. **Dense** — Ollama embeddings + cosine similarity  
2. **Lexical** — SQLite FTS5 BM25 over path / symbols / content  
3. **RRF fusion** — merge ranked lists from both  
4. **Multi-query** — separate queries for diff, symbols, and BLoC layers  
5. **Path expansion** — follow Dart imports + sibling contract/bloc/service/api/ui files  
6. **MMR** — diversify so results aren’t all from one file  

Chunk metadata stored in SQLite: `symbols`, `layer`, `imports` (schema v2).  
Opening an older `rag.sqlite` migrates metadata + builds FTS **without** re-embedding.  
Re-run `./scripts/pr-review-index.sh` only if you want a clean full rebuild.

## Edit behavior

Change review standards in `RULES.md`. Re-index after large refactors (`./scripts/pr-review-index.sh`) or rely on the merge Action for routine updates.
