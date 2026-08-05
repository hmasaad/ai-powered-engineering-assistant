#!/usr/bin/env python3
"""Local PR reviewer powered by Ollama + SQLite RAG + guardrails."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parents[1]
sys.path.insert(0, str(AGENT_DIR))

from guardrails import (  # noqa: E402
    DEFAULT_MIN_CONFIDENCE,
    GuardrailReport,
    assert_read_only_policy,
    build_guarded_file_context,
    extract_json_object,
    filter_changed_paths,
    filter_diff_to_paths,
    filter_findings,
    format_review_markdown,
    redact_secrets,
    scan_secrets,
    validate_review_payload,
)
from rag_store import (  # noqa: E402
    DEFAULT_DB,
    DEFAULT_EMBED_MODEL,
    advanced_retrieve,
    ensure_embed_model,
    format_hits,
)

RULES_PATH = AGENT_DIR / "RULES.md"
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
MAX_DIFF_CHARS = 80_000
MAX_FILES = 40
DEFAULT_TOP_K = int(os.environ.get("PR_REVIEW_TOP_K", "8"))


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def ensure_ollama(model: str) -> None:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"Ollama is not reachable at {OLLAMA_HOST}.\n"
            "Install: https://ollama.com\n"
            f"Then: ollama serve && ollama pull {model}\n"
            f"Error: {exc}\n"
        )
        sys.exit(1)

    models = {m.get("name", "") for m in payload.get("models", [])}
    short = model.split(":")[0]
    if model not in models and not any(m.startswith(short) for m in models):
        sys.stderr.write(
            f"Model '{model}' not found in Ollama.\n"
            f"Run: ollama pull {model}\n"
            f"Available: {', '.join(sorted(models)) or '(none)'}\n"
        )
        sys.exit(1)


def diff_spec(base: str, head: str) -> str:
    merge_base = run(["git", "merge-base", base, head])
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        return f"{merge_base.stdout.strip()}...{head}"
    return f"{base}...{head}"


def resolve_pr(pr_number: int, fallback_base: str) -> tuple[str, str, str]:
    """Fetch PR head and return (base_ref, head_ref, label). Read-only — never approve/merge."""
    head_ref = f"refs/pr-review/{pr_number}"
    label = f"PR #{pr_number}"

    fetch = run(["git", "fetch", "origin", f"pull/{pr_number}/head:{head_ref}"])
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout or "").strip()
        raise RuntimeError(
            f"Failed to fetch PR #{pr_number} from origin.\n"
            f"Make sure the remote is GitHub and the PR exists.\n{detail}"
        )

    base_ref = fallback_base
    gh = run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "baseRefName,title,url",
        ]
    )
    if gh.returncode == 0 and gh.stdout.strip():
        try:
            meta = json.loads(gh.stdout)
            base_name = meta.get("baseRefName") or "main"
            base_ref = f"origin/{base_name}"
            title = meta.get("title") or ""
            url = meta.get("url") or ""
            label = f"PR #{pr_number}"
            if title:
                label += f" — {title}"
            if url:
                label += f" ({url})"
        except json.JSONDecodeError:
            pass
    else:
        sys.stderr.write(
            "Note: `gh` unavailable or failed; using "
            f"--base {fallback_base} as the PR base.\n"
        )

    if base_ref.startswith("origin/"):
        run(["git", "fetch", "origin", base_ref.removeprefix("origin/")])

    return base_ref, head_ref, label


def raw_unified_diff(
    base: str,
    head: str = "HEAD",
    *,
    include_dirty: bool = True,
) -> str:
    """Unified diff body used for hunk/nearby parsing (no stat wrappers)."""
    spec = diff_spec(base, head)
    parts = [run(["git", "diff", spec]).stdout or ""]
    if include_dirty and head == "HEAD":
        staged = run(["git", "diff", "--cached"]).stdout or ""
        unstaged = run(["git", "diff"]).stdout or ""
        if staged.strip():
            parts.append(staged)
        if unstaged.strip():
            parts.append(unstaged)
    return "\n".join(parts)


def changed_files(
    base: str,
    head: str = "HEAD",
    *,
    include_dirty: bool = True,
) -> list[str]:
    spec = diff_spec(base, head)
    result = run(["git", "diff", "--name-only", spec])
    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if include_dirty and head == "HEAD":
        dirty = run(["git", "status", "--porcelain"])
        for line in dirty.stdout.splitlines():
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path and path not in files:
                files.append(path)
    return files[:MAX_FILES]


def file_content_at_ref(path: str, ref: str) -> str | None:
    result = run(["git", "show", f"{ref}:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def load_file(path: str, ref: str | None = None) -> str | None:
    if ref:
        return file_content_at_ref(path, ref)
    full = ROOT / path
    if not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def flutter_analyze() -> str:
    flutter = os.environ.get("FLUTTER_BIN")
    if not flutter:
        candidates = [
            Path.home() / "fvm/versions/3.29.0/bin/flutter",
            Path("/Users/apple/fvm/versions/3.29.0/bin/flutter"),
        ]
        which = run(["bash", "-lc", "command -v flutter"])
        if which.returncode == 0 and which.stdout.strip():
            flutter = which.stdout.strip()
        else:
            for candidate in candidates:
                if candidate.exists():
                    flutter = str(candidate)
                    break
    if not flutter:
        return "flutter not found; skipped analyze"

    result = run([flutter, "analyze"])
    out = (result.stdout or "") + (result.stderr or "")
    if len(out) > 20_000:
        out = out[:20_000] + "\n[analyze truncated]"
    return out.strip() or "(no analyze output)"


def retrieve_context(
    diff: str,
    paths: list[str],
    *,
    db_path: Path,
    embed_model: str,
    top_k: int,
) -> str:
    if not db_path.is_file():
        return (
            "(RAG index missing — run ./scripts/pr-review-index.sh to build "
            f"{db_path})"
        )
    try:
        ensure_embed_model(embed_model)
        print(
            "Advanced RAG: hybrid (vector+FTS) · multi-query · layer/import expand...",
            file=sys.stderr,
        )
        hits = advanced_retrieve(
            diff,
            paths,
            db_path=db_path,
            embed_model=embed_model,
            top_k=top_k,
        )
        return format_hits(hits)
    except RuntimeError as exc:
        return f"(RAG retrieve failed: {exc})"


def build_prompt(
    rules: str,
    diff: str,
    files: str,
    analyze: str,
    retrieved: str,
    reviewed_paths: list[str],
) -> str:
    path_list = "\n".join(f"- {p}" for p in reviewed_paths) or "- (none)"
    return f"""Review this local git change set for the salon_booking Flutter project.

{assert_read_only_policy()}

Follow RULES exactly. Use RETRIEVED CONTEXT for architecture and neighboring code.
Do not invent issues that are not supported by the diff, retrieved context, nearby file context, or analyze output.
Review ONLY these changed reviewable files:
{path_list}

Output MUST be a single JSON object matching the schema in RULES.
Do not approve, merge, or modify the PR.

# RULES
{rules}

# RETRIEVED CONTEXT (RAG — nearby architecture only)
{retrieved}

# FLUTTER ANALYZE
{analyze}

# DIFF (changed reviewable files only)
{diff}

# NEARBY CODE (hunks ± context for changed files only)
{files}
"""


def ollama_chat(model: str, prompt: str) -> str:
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict but fair local PR reviewer. "
                    "Follow the provided RULES and emit ONLY valid JSON "
                    "matching the review schema. "
                    "Ground findings in DIFF, RETRIEVED CONTEXT, and ANALYZE. "
                    "Never approve, merge, or modify PRs."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.stderr.write(f"Ollama HTTP error: {exc.code}\n{detail}\n")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Ollama request failed: {exc}\n")
        sys.exit(1)

    message = payload.get("message") or {}
    content = message.get("content")
    if not content:
        sys.stderr.write(f"Unexpected Ollama response: {payload}\n")
        sys.exit(1)
    return content.strip()


def apply_output_guardrails(
    raw: str,
    *,
    reviewed_paths: list[str],
    min_confidence: float,
    report: GuardrailReport,
) -> tuple[dict | None, str]:
    """Validate JSON, filter findings, return (payload|None, markdown_or_error)."""
    data = extract_json_object(raw)
    if data is None:
        report.schema_ok = False
        report.notes.append("Model output was not valid JSON")
        return None, (
            "## Guardrail failure\n"
            "Model output was not valid JSON. Raw output below.\n\n"
            f"```\n{raw[:8_000]}\n```"
        )

    errors = validate_review_payload(data)
    if errors:
        report.schema_ok = False
        report.notes.extend(errors[:20])
        return None, (
            "## Guardrail failure\n"
            "Output failed JSON schema validation:\n"
            + "\n".join(f"- {e}" for e in errors[:20])
            + "\n\n```json\n"
            + json.dumps(data, indent=2)[:8_000]
            + "\n```"
        )

    report.schema_ok = True
    filtered, low, invalid = filter_findings(
        data,
        allowed_files=set(reviewed_paths),
        min_confidence=min_confidence,
    )
    report.findings_dropped_low_confidence = low
    report.findings_dropped_invalid = invalid
    if low or invalid:
        report.notes.append(
            f"Dropped findings: low_confidence={low}, invalid_or_out_of_scope={invalid}"
        )
    return filtered, format_review_markdown(filtered)


def print_guardrail_summary(report: GuardrailReport) -> None:
    print("── Guardrails ──", file=sys.stderr)
    print(
        f"Reviewed files ({len(report.reviewed_paths)}): "
        f"{', '.join(report.reviewed_paths) or '(none)'}",
        file=sys.stderr,
    )
    if report.skipped_paths:
        print(
            f"Skipped generated/binary/deps ({len(report.skipped_paths)}): "
            f"{', '.join(report.skipped_paths[:20])}"
            + ("…" if len(report.skipped_paths) > 20 else ""),
            file=sys.stderr,
        )
    if report.secrets:
        kinds = sorted({s.kind for s in report.secrets})
        print(
            f"Secrets detected: {len(report.secrets)} "
            f"({', '.join(kinds)}); redacted={report.secrets_redacted}",
            file=sys.stderr,
        )
    print(f"Schema valid: {report.schema_ok}", file=sys.stderr)
    if report.findings_dropped_low_confidence or report.findings_dropped_invalid:
        print(
            "Filtered findings: "
            f"low_confidence={report.findings_dropped_low_confidence}, "
            f"invalid={report.findings_dropped_invalid}",
            file=sys.stderr,
        )
    print(assert_read_only_policy(), file=sys.stderr)
    for note in report.notes:
        print(f"Note: {note}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local Ollama PR reviewer with SQLite RAG + guardrails",
    )
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="GitHub PR number to review (fetches pull/<n>/head from origin)",
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PR_REVIEW_BASE", "origin/main"),
        help="Git base ref to diff against (default: origin/main; "
        "overridden by PR base when --pr and gh are available)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama chat model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--embed-model",
        default=DEFAULT_EMBED_MODEL,
        help=f"Ollama embed model (default: {DEFAULT_EMBED_MODEL})",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="SQLite RAG index path",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Number of RAG chunks to retrieve",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=float(
            os.environ.get("PR_REVIEW_MIN_CONFIDENCE", str(DEFAULT_MIN_CONFIDENCE))
        ),
        help=f"Drop findings below this confidence (default: {DEFAULT_MIN_CONFIDENCE})",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to write the validated JSON review payload",
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="Skip RAG retrieval",
    )
    parser.add_argument(
        "--dry-gather",
        action="store_true",
        help="Print gathered (redacted) context only; do not call chat model",
    )
    args = parser.parse_args()

    if not RULES_PATH.is_file():
        sys.stderr.write(f"Missing rules file: {RULES_PATH}\n")
        return 1

    report = GuardrailReport()
    rules = RULES_PATH.read_text(encoding="utf-8")
    head = "HEAD"
    base = args.base
    include_dirty = True
    file_ref: str | None = None
    target_label = "current branch / working tree"

    if args.pr is not None:
        print(f"Fetching PR #{args.pr} from origin (read-only)...", file=sys.stderr)
        try:
            base, head, target_label = resolve_pr(args.pr, args.base)
        except RuntimeError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        include_dirty = False
        file_ref = head

    print(f"Reviewing: {target_label}", file=sys.stderr)
    print(f"Diff range: {base}...{head}", file=sys.stderr)
    print("Gathering diff + applying guardrails...", file=sys.stderr)

    all_paths = changed_files(base, head, include_dirty=include_dirty)
    reviewed, skipped = filter_changed_paths(all_paths)
    report.reviewed_paths = reviewed
    report.skipped_paths = skipped

    if not reviewed:
        print_guardrail_summary(report)
        print(
            "## Summary\nNo reviewable changed files "
            "(only generated/binary/dependency paths, or empty diff).\n\n"
            "## Blockers\nNone\n\n## Should fix\nNone\n\n## Nits\nNone\n\n"
            "## Analyze notes\nnone"
        )
        return 0

    raw_diff = raw_unified_diff(base, head, include_dirty=include_dirty)
    allowed = set(reviewed)
    guarded_diff = filter_diff_to_paths(raw_diff, allowed)
    # Keep a short stat for humans in dry-gather
    display_diff = guarded_diff
    if len(display_diff) > MAX_DIFF_CHARS:
        display_diff = display_diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated]"

    files = build_guarded_file_context(
        reviewed,
        diff_text=raw_diff,
        file_loader=lambda p: load_file(p, file_ref),
    )
    analyze = flutter_analyze()

    if args.no_rag:
        retrieved = "(RAG disabled)"
    else:
        print("Retrieving RAG context from SQLite index...", file=sys.stderr)
        retrieved = retrieve_context(
            guarded_diff,
            reviewed,
            db_path=Path(args.db),
            embed_model=args.embed_model,
            top_k=args.top_k,
        )

    prompt = build_prompt(
        rules,
        display_diff,
        files,
        analyze,
        retrieved,
        reviewed,
    )

    # Secret scan + redact before any model call
    secret_hits = scan_secrets(prompt, path="(assembled prompt)")
    report.secrets = secret_hits
    prompt, n_redacted = redact_secrets(prompt)
    report.secrets_redacted = n_redacted
    if secret_hits:
        report.notes.append(
            "Secret-like patterns were redacted before sending context to the model"
        )

    print_guardrail_summary(report)

    if args.dry_gather:
        print(prompt)
        return 0

    ensure_ollama(args.model)
    print(f"Reviewing with Ollama model '{args.model}'...", file=sys.stderr)
    raw_review = ollama_chat(args.model, prompt)

    payload, markdown = apply_output_guardrails(
        raw_review,
        reviewed_paths=reviewed,
        min_confidence=args.min_confidence,
        report=report,
    )
    # Re-print filter notes after validation
    if report.findings_dropped_low_confidence or report.findings_dropped_invalid:
        print(
            "Post-filter: "
            f"low_confidence={report.findings_dropped_low_confidence}, "
            f"invalid={report.findings_dropped_invalid}",
            file=sys.stderr,
        )

    if args.json_out and payload is not None:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote validated JSON → {out_path}", file=sys.stderr)

    print(markdown)
    if payload is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
