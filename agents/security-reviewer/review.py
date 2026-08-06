#!/usr/bin/env python3
"""Local Security Reviewer: Ollama + shared RAG + security prechecks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parents[1]
PR_REVIEWER_DIR = AGENT_DIR.parent / "pr-reviewer"

# Prefer PR-reviewer shared libs; keep local agent dir for prechecks/report.
sys.path.insert(0, str(AGENT_DIR))
sys.path.insert(0, str(PR_REVIEWER_DIR))

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
from mutes import apply_mutes, load_mute_rules  # noqa: E402
from rag_store import (  # noqa: E402
    DEFAULT_DB,
    DEFAULT_EMBED_MODEL,
    advanced_retrieve,
    ensure_embed_model,
    format_hits,
)

# Load PR-reviewer helpers under a unique module name (avoid clashing with this file).
import importlib.util  # noqa: E402

_pr_spec = importlib.util.spec_from_file_location(
    "pr_reviewer_review",
    PR_REVIEWER_DIR / "review.py",
)
if _pr_spec is None or _pr_spec.loader is None:
    raise RuntimeError(f"Cannot load PR reviewer helpers from {PR_REVIEWER_DIR}")
pr_review = importlib.util.module_from_spec(_pr_spec)
sys.modules["pr_reviewer_review"] = pr_review
_pr_spec.loader.exec_module(pr_review)

# Local security-specific modules (explicit paths so PR-reviewer names don't win)
_pre_spec = importlib.util.spec_from_file_location(
    "sec_prechecks",
    AGENT_DIR / "prechecks.py",
)
assert _pre_spec and _pre_spec.loader
_pre = importlib.util.module_from_spec(_pre_spec)
sys.modules["sec_prechecks"] = _pre
_pre_spec.loader.exec_module(_pre)
format_prechecks_for_prompt = _pre.format_prechecks_for_prompt
precheck_evidence_ids = _pre.precheck_evidence_ids
run_prechecks = _pre.run_prechecks

_rep_spec = importlib.util.spec_from_file_location(
    "sec_report",
    AGENT_DIR / "report.py",
)
assert _rep_spec and _rep_spec.loader
_rep = importlib.util.module_from_spec(_rep_spec)
sys.modules["sec_report"] = _rep
_rep_spec.loader.exec_module(_rep)
build_report_payload = _rep.build_report_payload
write_report = _rep.write_report

RULES_PATH = AGENT_DIR / "RULES.md"
MUTES_PATH = AGENT_DIR / "mutes.yaml"
DEFAULT_TRIAGE_MODEL = os.environ.get("OLLAMA_TRIAGE_MODEL") or os.environ.get(
    "OLLAMA_MODEL", "llama3.2"
)
DEFAULT_STRONG_MODEL = os.environ.get("OLLAMA_STRONG_MODEL", DEFAULT_TRIAGE_MODEL)
DEFAULT_TOP_K = int(os.environ.get("SEC_REVIEW_TOP_K") or os.environ.get("PR_REVIEW_TOP_K", "8"))
SERIOUS = {"blocker", "should_fix"}
SYSTEM_PROMPT = (
    "You are a strict local Security Review Agent for Flutter. "
    "Detect security concerns only. Follow the provided RULES and emit ONLY "
    "valid JSON matching the review schema. Ground every finding with evidence. "
    "Never approve, merge, or modify PRs."
)


def build_prompt(
    rules: str,
    diff: str,
    files: str,
    analyze: str,
    retrieved: str,
    reviewed_paths: list[str],
    prechecks_text: str,
    *,
    mode: str = "full",
    candidate_findings: list[dict[str, Any]] | None = None,
) -> str:
    path_list = "\n".join(f"- {p}" for p in reviewed_paths) or "- (none)"
    if mode == "strong":
        candidates = json.dumps(candidate_findings or [], indent=2)
        return f"""You are the strong second-pass Security Reviewer for salon_booking.

{assert_read_only_policy()}

Re-evaluate ONLY these serious security candidate findings from triage.
Keep a finding only if evidence is solid; drop or downgrade weak ones.
Return full JSON schema (summary, analyze_notes, findings) with residual + confirmed serious issues.
Do not invent style/architecture nits unless they clearly create an exploit or data-exposure path.

# CANDIDATE FINDINGS
{candidates}

# RULES
{rules}

# DETERMINISTIC PRECHECKS (already filed — do not duplicate)
{prechecks_text}

# RETRIEVED CONTEXT
{retrieved}

# FLUTTER ANALYZE
{analyze}

# DIFF
{diff}

# NEARBY CODE
{files}
"""

    return f"""Review this local git change set for Flutter SECURITY concerns only.

{assert_read_only_policy()}

Follow RULES exactly. Use RETRIEVED CONTEXT for neighboring auth/service/API call sites.
Do not invent issues that are not supported by the diff, retrieved context, nearby file context, prechecks, or analyze output.
Focus on secrets, transport trust, insecure storage, sensitive logging, WebView/deep-link risks, and auth/session gaps.
Review ONLY these changed reviewable files:
{path_list}

Output MUST be a single JSON object matching the schema in RULES.
Every finding needs grounded evidence.
Do not approve, merge, or modify the PR.

# RULES
{rules}

# DETERMINISTIC PRECHECKS (already filed — do not duplicate)
{prechecks_text}

# RETRIEVED CONTEXT (RAG — nearby architecture only)
{retrieved}

# FLUTTER ANALYZE
{analyze}

# DIFF (changed reviewable files only)
{diff}

# NEARBY CODE (hunks ± context for changed files only)
{files}
"""


def apply_output_guardrails(
    raw: str,
    *,
    reviewed_paths: list[str],
    min_confidence: float,
    report: GuardrailReport,
    diff_text: str,
    retrieved_text: str,
    analyze_text: str,
    allowed_evidence: set[str],
) -> tuple[dict | None, str]:
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
    filtered, low, invalid, no_evidence = filter_findings(
        data,
        allowed_files=set(reviewed_paths),
        min_confidence=min_confidence,
        diff_text=diff_text,
        retrieved_text=retrieved_text,
        analyze_text=analyze_text,
        allowed_evidence=allowed_evidence,
    )
    report.findings_dropped_low_confidence = low
    report.findings_dropped_invalid = invalid
    report.findings_dropped_no_evidence = no_evidence
    if low or invalid or no_evidence:
        report.notes.append(
            f"Dropped findings: low_confidence={low}, "
            f"invalid_or_out_of_scope={invalid}, no_evidence={no_evidence}"
        )
    return filtered, format_review_markdown(filtered)


def merge_findings(
    model_payload: dict[str, Any] | None,
    precheck_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    base = {
        "summary": (model_payload or {}).get("summary")
        or "Deterministic security prechecks only (model produced no valid summary).",
        "analyze_notes": (model_payload or {}).get("analyze_notes") or "none",
        "findings": [],
    }
    seen: set[str] = set()
    for item in list(precheck_findings) + list((model_payload or {}).get("findings") or []):
        key = (
            f"{item.get('file')}:{item.get('line')}:{item.get('severity')}:"
            f"{item.get('check_id') or item.get('explanation', '')[:40]}"
        )
        if key in seen:
            continue
        seen.add(key)
        base["findings"].append(item)
    return base


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local Ollama Security Reviewer — detect secrets, transport, "
            "storage, logging, and trust-boundary risks"
        ),
    )
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument(
        "--base",
        default=os.environ.get("SEC_REVIEW_BASE")
        or os.environ.get("PR_REVIEW_BASE", "origin/main"),
    )
    parser.add_argument("--model", default=DEFAULT_TRIAGE_MODEL)
    parser.add_argument("--strong-model", default=DEFAULT_STRONG_MODEL)
    parser.add_argument("--no-routing", action="store_true")
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=float(
            os.environ.get(
                "SEC_REVIEW_MIN_CONFIDENCE",
                os.environ.get("PR_REVIEW_MIN_CONFIDENCE", str(DEFAULT_MIN_CONFIDENCE)),
            )
        ),
    )
    parser.add_argument("--json-out", default="")
    parser.add_argument("--no-rag", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--dry-gather", action="store_true")
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
    pr_number = args.pr

    if args.pr is not None:
        print(f"Fetching PR #{args.pr} from origin (read-only)...", file=sys.stderr)
        try:
            base, head, target_label = pr_review.resolve_pr(args.pr, args.base)
        except RuntimeError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        include_dirty = False
        file_ref = head

    print(f"Security review: {target_label}", file=sys.stderr)
    print(f"Diff range: {base}...{head}", file=sys.stderr)
    print("Gathering diff + applying guardrails...", file=sys.stderr)

    all_paths = pr_review.changed_files(base, head, include_dirty=include_dirty)
    reviewed, skipped = filter_changed_paths(all_paths)
    report.reviewed_paths = reviewed
    report.skipped_paths = skipped

    empty_payload = {
        "summary": "No reviewable changed files "
        "(only generated/binary/dependency paths, or empty diff).",
        "analyze_notes": "none",
        "findings": [],
    }

    if not reviewed:
        pr_review.print_guardrail_summary(report)
        print(format_review_markdown(empty_payload))
        if not args.no_report:
            write_report(
                build_report_payload(
                    label=target_label,
                    pr=pr_number,
                    base=base,
                    head=head,
                    triage_model=args.model,
                    strong_model=args.strong_model,
                    routing_used=False,
                    reviewed_paths=reviewed,
                    skipped_paths=skipped,
                    payload=empty_payload,
                    precheck_count=0,
                    muted_count=0,
                    guardrail_notes=report.notes,
                ),
                open_browser=not args.no_open,
            )
        return 0

    raw_diff = pr_review.raw_unified_diff(base, head, include_dirty=include_dirty)
    allowed = set(reviewed)
    guarded_diff = filter_diff_to_paths(raw_diff, allowed)
    display_diff = guarded_diff
    if len(display_diff) > pr_review.MAX_DIFF_CHARS:
        display_diff = display_diff[: pr_review.MAX_DIFF_CHARS] + "\n\n[diff truncated]"

    loader = lambda p: pr_review.load_file(p, file_ref)  # noqa: E731
    files = build_guarded_file_context(
        reviewed,
        diff_text=raw_diff,
        file_loader=loader,
    )
    print("Running flutter analyze + security prechecks...", file=sys.stderr)
    analyze = pr_review.flutter_analyze()
    precheck_objs = run_prechecks(
        reviewed_paths=reviewed,
        diff_text=raw_diff,
        analyze_text=analyze,
        loader=loader,
        root=ROOT,
    )
    precheck_findings = [p.as_finding() for p in precheck_objs]
    prechecks_text = format_prechecks_for_prompt(precheck_objs)
    allowed_evidence = precheck_evidence_ids(precheck_objs)
    print(f"Security prechecks: {len(precheck_findings)} finding(s)", file=sys.stderr)

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
        prechecks_text,
    )

    secret_hits = scan_secrets(prompt, path="(assembled prompt)")
    report.secrets = secret_hits
    prompt, n_redacted = redact_secrets(prompt)
    report.secrets_redacted = n_redacted
    if secret_hits:
        report.notes.append(
            "Secret-like patterns were redacted before sending context to the model"
        )

    pr_review.print_guardrail_summary(report)

    if args.dry_gather:
        print(prompt)
        return 0

    triage_model = args.model
    strong_model = pr_review.resolve_model(args.strong_model, triage_model)
    pr_review.ensure_ollama(triage_model)
    if strong_model != triage_model:
        pr_review.ensure_ollama(strong_model)

    print(f"Triage pass with '{triage_model}'...", file=sys.stderr)
    raw_review = pr_review.ollama_chat(triage_model, prompt, system=SYSTEM_PROMPT)
    payload, _markdown = apply_output_guardrails(
        raw_review,
        reviewed_paths=reviewed,
        min_confidence=args.min_confidence,
        report=report,
        diff_text=display_diff,
        retrieved_text=retrieved,
        analyze_text=analyze,
        allowed_evidence=allowed_evidence,
    )

    routing_used = False
    if payload is not None and not args.no_routing and strong_model:
        serious = [
            f for f in payload.get("findings") or [] if f.get("severity") in SERIOUS
        ]
        if serious and strong_model != triage_model:
            routing_used = True
            print(
                f"Strong pass with '{strong_model}' on {len(serious)} serious finding(s)...",
                file=sys.stderr,
            )
            strong_prompt = build_prompt(
                rules,
                display_diff,
                files,
                analyze,
                retrieved,
                reviewed,
                prechecks_text,
                mode="strong",
                candidate_findings=serious,
            )
            strong_prompt, _ = redact_secrets(strong_prompt)
            raw_strong = pr_review.ollama_chat(
                strong_model,
                strong_prompt,
                system=(
                    "You are the strong second-pass Security Reviewer. "
                    "Emit ONLY valid JSON. Drop weak serious findings. "
                    "Never approve or merge PRs."
                ),
            )
            strong_payload, _ = apply_output_guardrails(
                raw_strong,
                reviewed_paths=reviewed,
                min_confidence=args.min_confidence,
                report=report,
                diff_text=display_diff,
                retrieved_text=retrieved,
                analyze_text=analyze,
                allowed_evidence=allowed_evidence,
            )
            if strong_payload is not None:
                nits = [
                    f
                    for f in (payload.get("findings") or [])
                    if f.get("severity") == "nit"
                ]
                payload = {
                    "summary": strong_payload.get("summary") or payload.get("summary"),
                    "analyze_notes": strong_payload.get("analyze_notes")
                    or payload.get("analyze_notes"),
                    "findings": list(strong_payload.get("findings") or []) + nits,
                }
        elif serious and strong_model == triage_model:
            report.notes.append(
                "Strong model unavailable/same as triage — skipped routing"
            )

    merged = merge_findings(payload, precheck_findings)

    mute_rules = load_mute_rules(MUTES_PATH)
    kept, muted = apply_mutes(merged.get("findings") or [], mute_rules)
    report.findings_muted = len(muted)
    if muted:
        report.notes.append(
            "Muted: " + ", ".join(sorted({m.get("muted_by", "?") for m in muted}))
        )
    merged["findings"] = kept

    markdown = format_review_markdown(merged)
    print(markdown)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote validated JSON → {out_path}", file=sys.stderr)

    if not args.no_report:
        html_path = write_report(
            build_report_payload(
                label=target_label,
                pr=pr_number,
                base=base,
                head=head,
                triage_model=triage_model,
                strong_model=strong_model,
                routing_used=routing_used,
                reviewed_paths=reviewed,
                skipped_paths=skipped,
                payload=merged,
                precheck_count=len(precheck_findings),
                muted_count=len(muted),
                guardrail_notes=report.notes
                + [
                    f"routing_used={routing_used}",
                    f"prechecks={len(precheck_findings)}",
                    "agent=security-reviewer",
                ],
            ),
            open_browser=not args.no_open,
        )
        print(f"Report → {html_path}", file=sys.stderr)

    if payload is None and not precheck_findings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
