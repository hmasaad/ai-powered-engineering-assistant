# Local Performance Reviewer (Ollama + shared SQLite RAG)

Detects **performance concerns** on git changes using Flutter-focused rules, deterministic prechecks, and the same RAG index as the PR reviewer. Does **not** use Cursor Cloud.

## Setup

Same as the PR reviewer:

```bash
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
./scripts/pr-review-index.sh   # shared index
```

## What it checks

Deterministic prechecks (before LLM):

| Check | Signal |
|-------|--------|
| `image_network_uncached` | `Image.network` without cache size hints |
| `eager_listview` | `ListView(children: …)` for dynamic lists |
| `shrink_wrap` | `shrinkWrap: true` on UI scrollables |
| `bloc_builder_no_build_when` | `BlocBuilder` without `buildWhen` |
| `future_in_build` | `FutureBuilder(future: call())` |
| `sync_heavy_work` | sync JSON/file work on UI/bloc paths |
| `opacity_widget` | `Opacity(` added in the diff |
| `nested_listview` | nested scrollables in UI |

Then the model looks for residual jank / rebuild / isolate / image / list risks only.

## Run

```bash
./scripts/perf-review.sh
./scripts/perf-review.sh --pr 42
./scripts/perf-review.sh --dry-gather
```

Reports land in `agents/performance-reviewer/reports/` (HTML + JSON).

### Useful options

| Flag / env | Meaning |
|---|---|
| `--pr 42` | Review GitHub PR #42 |
| `--base origin/main` | Diff base (`PERF_REVIEW_BASE` / `PR_REVIEW_BASE`) |
| `--model llama3.2` | Triage model |
| `--strong-model …` | Strong pass for serious findings |
| `--no-routing` | Skip triage→strong routing |
| `--min-confidence 0.55` | Drop low-confidence findings |
| `--no-rag` | Skip retrieval |
| `--no-report` | Skip HTML/JSON report pack |
| `--dry-gather` | Print redacted prompt context only |

## Shared pieces

- RAG index: `agents/pr-reviewer/index/rag.sqlite`
- Guardrails / mutes plumbing: imported from `agents/pr-reviewer`
- Performance rules: `RULES.md`
- Mutes: `mutes.yaml`

Edit `RULES.md` to tighten performance standards. Re-index after large refactors via `./scripts/pr-review-index.sh`.
