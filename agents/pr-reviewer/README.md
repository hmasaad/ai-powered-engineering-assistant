# Local PR Reviewer (Ollama)

Reviews git changes on your machine using project rules in `RULES.md`. Does **not** use Cursor Cloud.

## Setup

1. Install [Ollama](https://ollama.com)
2. Start it and pull a model:

```bash
ollama serve
ollama pull llama3.2
```

Optional stronger models: `llama3.1:8b`, `qwen2.5-coder:7b`, `deepseek-coder-v2`.

## Run

From the repo root:

```bash
./scripts/pr-review.sh
```

Or:

```bash
python3 agents/pr-reviewer/review.py --base origin/main --model llama3.2
```

### Useful flags / env

| Flag / env | Meaning |
|---|---|
| `--base origin/main` | Diff base (also `PR_REVIEW_BASE`) |
| `--model qwen2.5-coder:7b` | Ollama model (also `OLLAMA_MODEL`) |
| `OLLAMA_HOST` | Default `http://127.0.0.1:11434` |
| `--dry-gather` | Print prompt context only |

## What it does

1. Reads `agents/pr-reviewer/RULES.md`
2. Collects git diff vs base + dirty files
3. Attaches changed file contents (truncated)
4. Runs `flutter analyze`
5. Asks Ollama for a structured review

Edit `RULES.md` to change review behavior for this project.
