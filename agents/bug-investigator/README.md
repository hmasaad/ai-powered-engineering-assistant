# Local Bug Investigator (Ollama + shared SQLite RAG)

Investigates **bugs from symptoms / stacktraces** (optional PR/diff scope) using Flutter BLoC-aware rules, deterministic bug-pattern prechecks, and the same RAG index as the PR reviewer. Does **not** use Cursor Cloud.

## Setup

```bash
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
./scripts/pr-review-index.sh   # shared index
```

## What it does

1. Ingest `--bug` symptom and/or `--stacktrace`
2. Optionally scope to a PR/branch diff (`--pr`)
3. Run flutter analyze + deterministic bug prechecks
4. Retrieve related code via hybrid RAG (bug text + stack paths + diff)
5. Ask Ollama for hypotheses → likely root cause → repro steps → fix guidance
6. Emit markdown + HTML/JSON report pack

### Deterministic prechecks

| Check | Signal |
|-------|--------|
| `stack_frame` | Paths/lines cited in the stacktrace |
| `empty_catch` | Empty `catch` / `on` blocks |
| `ignored_response_exception` | Using `.data` without `.exception` |
| `null_bang` | `value!` assertions in UI |
| `setstate_unmounted` | `setState` after await without `mounted` |
| `fire_and_forget_async` | Un-awaited async loader calls in blocs |
| `ui_missing_error_branch` | BlocBuilder UI with no error state |

## Run

```bash
# Symptom-driven (repo RAG)
./scripts/bug-investigate.sh --bug "Confirm booking does nothing after selecting a slot"

# Stacktrace file
./scripts/bug-investigate.sh --stacktrace /tmp/flutter_crash.txt

# Symptom + PR scope
./scripts/bug-investigate.sh --bug "List does not refresh" --pr 42

# Hunt bugs introduced in a PR (no explicit symptom)
./scripts/bug-investigate.sh --pr 42

# Inspect gathered prompt only
./scripts/bug-investigate.sh --bug "..." --dry-gather --no-rag
```

Reports land in `agents/bug-investigator/reports/`.

### Useful options

| Flag / env | Meaning |
|---|---|
| `--bug "..."` | Symptom text or path to a text file |
| `--stacktrace ...` | Stacktrace/log text or file path |
| `--pr 42` | Scope to GitHub PR #42 |
| `--whole-repo` | Prefer broad lib/ precheck scope |
| `--base origin/main` | Diff base |
| `--model llama3.2` | Triage model |
| `--strong-model …` | Strong pass for serious findings |
| `--min-confidence 0.55` | Drop low-confidence findings |
| `--dry-gather` | Print redacted prompt context only |

## Shared pieces

- RAG index: `agents/pr-reviewer/index/rag.sqlite`
- Guardrails / mutes plumbing: imported from `agents/pr-reviewer`
- Investigation rules: `RULES.md`
- Mutes: `mutes.yaml`

Edit `RULES.md` to tighten investigation standards.
