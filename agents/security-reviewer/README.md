# Local Security Reviewer (Ollama + shared SQLite RAG)

Detects **security concerns** on git changes using Flutter-focused rules, deterministic prechecks, and the same RAG index as the PR reviewer. Does **not** use Cursor Cloud.

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
| `hardcoded_secret_assignment` / key literals | API keys, tokens, private key blocks in source |
| `cleartext_http` | Non-loopback `http://` endpoints |
| `insecure_prefs_secret` | Tokens/secrets written to SharedPreferences |
| `sensitive_logging` | `print` / `debugPrint` of passwords/tokens/PII |
| `bad_certificate_callback` / TLS overrides | Certificate validation bypass |
| `webview_js_unrestricted` / `webview_file_access` | Risky WebView settings |
| `unvalidated_url_launch` | `launchUrl` without nearby scheme/allowlist checks |
| `debug_security_bypass` | Auth/TLS bypasses added behind debug flags |
| `path_concat` | File paths built via string concatenation |

Then the model looks for residual secrets / transport / storage / auth / deep-link risks only.

## Run

```bash
./scripts/sec-review.sh
./scripts/sec-review.sh --pr 42
./scripts/sec-review.sh --dry-gather
```

Reports land in `agents/security-reviewer/reports/` (HTML + JSON).

### Useful options

| Flag / env | Meaning |
|---|---|
| `--pr 42` | Review GitHub PR #42 |
| `--base origin/main` | Diff base (`SEC_REVIEW_BASE` / `PR_REVIEW_BASE`) |
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
- Security rules: `RULES.md`
- Mutes: `mutes.yaml`

Edit `RULES.md` to tighten security standards. Re-index after large refactors via `./scripts/pr-review-index.sh`.
