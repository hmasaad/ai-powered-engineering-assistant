#!/usr/bin/env python3
"""Local PR reviewer powered by Ollama + SQLite RAG + project RULES.md."""

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
MAX_FILE_CHARS = 12_000
MAX_FILES = 20
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
    """Fetch PR head and return (base_ref, head_ref, label)."""
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

    # Ensure base ref exists locally when possible
    if base_ref.startswith("origin/"):
        run(["git", "fetch", "origin", base_ref.removeprefix("origin/")])

    return base_ref, head_ref, label


def git_diff(base: str, head: str = "HEAD", *, include_dirty: bool = True) -> str:
    spec = diff_spec(base, head)
    ranged = run(["git", "diff", "--stat", spec])
    full = run(["git", "diff", spec])

    parts = [
        f"### Range diff ({spec})\n{full.stdout or '(empty)'}",
        f"### Diff stat\n{ranged.stdout or '(empty)'}",
    ]
    if include_dirty and head == "HEAD":
        staged = run(["git", "diff", "--cached"])
        unstaged = run(["git", "diff"])
        if staged.stdout.strip():
            parts.append(f"### Staged (uncommitted)\n{staged.stdout}")
        if unstaged.stdout.strip():
            parts.append(f"### Unstaged (uncommitted)\n{unstaged.stdout}")

    text = "\n\n".join(parts)
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS] + "\n\n[diff truncated]"
    return text


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
    content = result.stdout
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n[file truncated]"
    return content


def file_context(paths: list[str], ref: str | None = None) -> str:
    allowed = {
        ".dart",
        ".yaml",
        ".yml",
        ".json",
        ".md",
        ".gradle",
        ".kt",
        ".swift",
    }
    chunks: list[str] = []
    for path in paths:
        if Path(path).suffix not in allowed:
            continue
        if ref:
            content = file_content_at_ref(path, ref)
            if content is None:
                continue
            chunks.append(f"### {path} @{ref}\n```\n{content}\n```")
            continue

        full = ROOT / path
        if not full.is_file():
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n[file truncated]"
        chunks.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(chunks) if chunks else "(no file context)"


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
) -> str:
    return f"""Review this local git change set for the salon_booking Flutter project.

Follow RULES exactly. Use RETRIEVED CONTEXT for architecture and neighboring code.
Do not invent issues that are not supported by the diff, retrieved context, file context, or analyze output.

# RULES
{rules}

# RETRIEVED CONTEXT (RAG)
{retrieved}

# FLUTTER ANALYZE
{analyze}

# DIFF
{diff}

# CHANGED FILE CONTEXT
{files}
"""


def ollama_chat(model: str, prompt: str) -> str:
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict but fair local PR reviewer. "
                    "Follow the provided RULES and output format. "
                    "Ground findings in DIFF, RETRIEVED CONTEXT, and ANALYZE."
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local Ollama PR reviewer with SQLite RAG",
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
        "--no-rag",
        action="store_true",
        help="Skip RAG retrieval",
    )
    parser.add_argument(
        "--dry-gather",
        action="store_true",
        help="Print gathered context only; do not call chat model",
    )
    args = parser.parse_args()

    if not RULES_PATH.is_file():
        sys.stderr.write(f"Missing rules file: {RULES_PATH}\n")
        return 1

    rules = RULES_PATH.read_text(encoding="utf-8")
    head = "HEAD"
    base = args.base
    include_dirty = True
    file_ref: str | None = None
    target_label = "current branch / working tree"

    if args.pr is not None:
        print(f"Fetching PR #{args.pr} from origin...", file=sys.stderr)
        try:
            base, head, target_label = resolve_pr(args.pr, args.base)
        except RuntimeError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        include_dirty = False
        file_ref = head

    print(f"Reviewing: {target_label}", file=sys.stderr)
    print(f"Diff range: {base}...{head}", file=sys.stderr)
    print("Gathering diff + file context + flutter analyze...", file=sys.stderr)
    diff = git_diff(base, head, include_dirty=include_dirty)
    paths = changed_files(base, head, include_dirty=include_dirty)
    files = file_context(paths, ref=file_ref)
    analyze = flutter_analyze()

    if args.no_rag:
        retrieved = "(RAG disabled)"
    else:
        print("Retrieving RAG context from SQLite index...", file=sys.stderr)
        retrieved = retrieve_context(
            diff,
            paths,
            db_path=Path(args.db),
            embed_model=args.embed_model,
            top_k=args.top_k,
        )

    prompt = build_prompt(rules, diff, files, analyze, retrieved)

    if args.dry_gather:
        print(prompt)
        return 0

    ensure_ollama(args.model)
    print(f"Reviewing with Ollama model '{args.model}'...", file=sys.stderr)
    review = ollama_chat(args.model, prompt)
    print(review)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
