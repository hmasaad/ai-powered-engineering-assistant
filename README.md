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

1. Indexing the repo into a local SQLite vector store
2. Retrieving the most relevant code chunks for the change
3. Passing those chunks into the review prompt with the diff and rules

So feedback can respect this project’s BLoC layering instead of generic Flutter advice.

### How it works

```
┌─────────────────┐
│  Index (once /  │  chunk lib/** + rules → Ollama embeddings
│  after refactors)│  → agents/pr-reviewer/index/rag.sqlite
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Review run     │
│  1. Load RULES.md
│  2. Collect git diff (+ optional --pr)
│  3. Run flutter analyze
│  4. Embed query from paths/symbols/diff
│  5. Retrieve top-k chunks (cosine similarity)
│  6. Ask local Ollama chat model
│  7. Print Summary / Blockers / Should fix / Nits
└─────────────────┘
```

| Piece | Role |
|--------|------|
| `agents/pr-reviewer/RULES.md` | Project review standards (architecture, tone, output format) |
| `agents/pr-reviewer/rag_store.py` | Chunking, embeddings, SQLite search |
| `agents/pr-reviewer/index.py` | Builds/refreshes the vector index |
| `agents/pr-reviewer/review.py` | Orchestrates gather → retrieve → review |
| `rag.sqlite` | Local index (gitignored) |
| Ollama | Embeddings (`nomic-embed-text`) + chat (`llama3.2`) on your machine |

### Setup

```bash
# Install Ollama: https://ollama.com
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text

# build/refresh vector index (re-run after large refactors)
./scripts/pr-review-index.sh
```

### Review

```bash
# current branch vs origin/main (+ dirty files)
./scripts/pr-review.sh

# specific GitHub PR (fetches pull/<n>/head; does not change your checkout)
./scripts/pr-review.sh --pr 42
```

When the review finishes, it writes a **coverage-style HTML report** (committed under `agents/pr-reviewer/reports/`) and opens it:

```
agents/pr-reviewer/reports/
  index.html          # dashboard of all reviewed PRs
  pr-42/index.html    # this PR’s visual review (overwritten on each run)
  pr-42/report.json
  latest/index.html   # most recent run
```

Same idea as bloc coverage (`flutter test --coverage` → `genhtml` → open HTML):
each `./scripts/pr-review.sh --pr N` refreshes that PR’s report until you stop
running it (typically until the PR is merged).

Useful flags: `--base`, `--model`, `--embed-model`, `--top-k`, `--no-rag`, `--no-open`.
