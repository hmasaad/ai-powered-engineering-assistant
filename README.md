# SalonBook

Flutter salon booking app using the same **contract-driven BLoC** architecture as our other apps (BaseBloc, feature contracts, get_it DI, services over APIs, `ScreenState` + `ViewActions`).

## Features

- Browse salons (sample JSON API)
- Salon details with services and stylists
- Book a service (stylist + day + time slot)
- View / cancel bookings

## Architecture

```
UI (BaseState) → Bloc (BaseBloc) → Service → Api → ResponseEntity
                     ↓
               ViewActions (navigation / toasts)
```

Sample data: `assets/data/salon_booking.json`

## Run

```bash
flutter pub get
flutter run
```

## Local PR Reviewer (Ollama + SQLite RAG)

A **local** PR review agent that runs on your machine — no Cursor Cloud. It uses project rules, `flutter analyze`, and **RAG** (Retrieval-Augmented Generation) so reviews are grounded in *this* codebase, not only the diff.

### Why RAG?

A diff-only review often misses context. In this app, a change in `salon_list_bloc.dart` may depend on the matching **contract**, **service**, **API**, and **UI/`BaseState`** patterns elsewhere.

Without retrieval, the model mostly sees the patch and guesses — more noise and missed issues.

RAG fixes that by:

1. Indexing the repo into a local SQLite store (vectors + FTS)
2. Advanced retrieval (hybrid search, multi-query, architecture path expansion)
3. Passing those chunks into the review prompt with the diff and rules

So feedback can respect this project’s BLoC layering instead of generic Flutter advice.

Guardrails (v1) keep reviews scoped to changed reviewable files, redact secrets, require schema-valid evidence-bound findings with confidence, support mutes, and keep the agent read-only (never approve/merge).

High-impact upgrades: deterministic prechecks → triage model → optional strong-model routing → HTML/JSON report pack.

### How it works

```
┌─────────────────┐
│  Index          │  full rebuild locally, or incremental on merge to main
│  (RAG sqlite)   │  → agents/pr-reviewer/index/rag.sqlite (committed)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Review run     │
│  1. Load RULES.md
│  2. Collect git diff (+ optional --pr)
│  3. Run flutter analyze
│  4. Embed query from paths/symbols/diff
│  5. Advanced RAG: hybrid vector+FTS, multi-query, MMR
│  6. Ask local Ollama chat model
│  7. Print Summary / Blockers / Should fix / Nits
└─────────────────┘
```

| Piece | Role |
|--------|------|
| `agents/pr-reviewer/RULES.md` | Project review standards (architecture, tone, output format) |
| `agents/pr-reviewer/rag_store.py` | Advanced RAG: hybrid vector+FTS, RRF, MMR, metadata |
| `agents/pr-reviewer/index.py` | Full + incremental SQLite index updates |
| `agents/pr-reviewer/review.py` | Orchestrates gather → retrieve → review |
| `agents/pr-reviewer/index/rag.sqlite` | Vector index (committed; refreshed on merge to main) |
| `.github/workflows/rag-index-on-merge.yml` | Incremental re-embed when PRs land on main |
| Ollama | Embeddings (`nomic-embed-text`) + chat (`llama3.2`) |

### Setup

```bash
# Install Ollama: https://ollama.com
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text

# full rebuild (local)
./scripts/pr-review-index.sh
```

### RAG updates when a PR merges

```
PR merges into main
        │
        ▼
GitHub Action (push to main)
        │
        ▼
Diff before...after → modified / deleted files only
        │
        ▼
Delete old SQLite chunks for those paths
        │
        ▼
Re-embed updated file content with Ollama
        │
        ▼
Commit agents/pr-reviewer/index/rag.sqlite back to main
        │
        ▼
Future ./scripts/pr-review.sh uses the latest index (after git pull)
```

Local incremental (same as CI):

```bash
./scripts/pr-review-index-update.sh --since <before_sha> --until HEAD
```

Manual full rebuild anytime: `./scripts/pr-review-index.sh`

### Review

```bash
# current branch vs origin/main (+ dirty files)
./scripts/pr-review.sh

# specific GitHub PR (fetches pull/<n>/head; does not change your checkout)
./scripts/pr-review.sh --pr 42

# inspect gathered context without calling the chat model
./scripts/pr-review.sh --pr 42 --dry-gather
```

Useful flags: `--base`, `--model`, `--embed-model`, `--top-k`, `--no-rag`.

More detail: `agents/pr-reviewer/README.md`

## Local Performance Reviewer (detect performance concerns)

Sibling agent to the PR reviewer. Same Ollama + shared SQLite RAG pipeline, but focused on Flutter performance: rebuild waste, list/scroll cost, images, main-isolate stalls, and related jank risks.

```bash
# uses the same RAG index as the PR reviewer
./scripts/perf-review.sh
./scripts/perf-review.sh --pr 42
./scripts/perf-review.sh --dry-gather
```

| Piece | Role |
|--------|------|
| `agents/performance-reviewer/RULES.md` | Performance review standards |
| `agents/performance-reviewer/prechecks.py` | Deterministic Flutter perf heuristics |
| `agents/performance-reviewer/review.py` | Orchestrates gather → retrieve → review |
| `agents/pr-reviewer/index/rag.sqlite` | Shared vector/FTS index |
| `agents/performance-reviewer/reports/` | HTML/JSON report pack |

More detail: `agents/performance-reviewer/README.md`
