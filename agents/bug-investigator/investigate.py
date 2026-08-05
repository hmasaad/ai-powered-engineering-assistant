#!/usr/bin/env python3
"""Local Bug Investigator: Ollama + shared RAG + bug-pattern prechecks."""

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
    redact_secrets,
    scan_secrets,
    _validate,
)
from mutes import apply_mutes, load_mute_rules  # noqa: E402
from rag_store import (  # noqa: E402
    DEFAULT_DB,
    DEFAULT_EMBED_MODEL,
    advanced_retrieve,
    embed_text,
    ensure_embed_model,
    expand_prefer_paths,
    format_hits,
    search,
)

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

_pre_spec = importlib.util.spec_from_file_location(
    "bug_prechecks",
    AGENT_DIR / "prechecks.py",
)
assert _pre_spec and _pre_spec.loader
_pre = importlib.util.module_from_spec(_pre_spec)
sys.modules["bug_prechecks"] = _pre
_pre_spec.loader.exec_module(_pre)
format_prechecks_for_prompt = _pre.format_prechecks_for_prompt
parse_stack_paths = _pre.parse_stack_paths
precheck_evidence_ids = _pre.precheck_evidence_ids
run_prechecks = _pre.run_prechecks
default_scope_paths = _pre.default_scope_paths

_rep_spec = importlib.util.spec_from_file_location(
    "bug_report",
    AGENT_DIR / "report.py",
)
assert _rep_spec and _rep_spec.loader
_rep = importlib.util.module_from_spec(_rep_spec)
sys.modules["bug_report"] = _rep
_rep_spec.loader.exec_module(_rep)
build_report_payload = _rep.build_report_payload
write_report = _rep.write_report

RULES_PATH = AGENT_DIR / "RULES.md"
MUTES_PATH = AGENT_DIR / "mutes.yaml"
SCHEMA_PATH = AGENT_DIR / "schema" / "investigation_output.schema.json"
DEFAULT_TRIAGE_MODEL = os.environ.get("OLLAMA_TRIAGE_MODEL") or os.environ.get(
    "OLLAMA_MODEL", "llama3.2"
)
DEFAULT_STRONG_MODEL = os.environ.get("OLLAMA_STRONG_MODEL", DEFAULT_TRIAGE_MODEL)
DEFAULT_TOP_K = int(
    os.environ.get("BUG_INVESTIGATE_TOP_K")
    or os.environ.get("PR_REVIEW_TOP_K", "10")
)
SERIOUS = {"blocker", "should_fix"}
SYSTEM_PROMPT = (
    "You are a strict local Bug Investigation Agent for Flutter. "
    "Trace symptoms to root causes with evidence. Follow RULES and emit ONLY "
    "valid JSON matching the investigation schema. Never approve, merge, or modify code."
)

INVESTIGATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "summary",
        "bug_statement",
        "analyze_notes",
        "hypotheses",
        "likely_root_cause",
        "reproduction_steps",
        "recommended_fix",
        "findings",
    ],
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "bug_statement": {"type": "string", "minLength": 1},
        "analyze_notes": {"type": "string"},
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "claim",
                    "likelihood",
                    "confidence",
                    "status",
                    "evidence",
                ],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "claim": {"type": "string", "minLength": 1},
                    "likelihood": {"enum": ["high", "medium", "low"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "status": {"enum": ["likely", "possible", "ruled_out"]},
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 3},
                    },
                },
            },
        },
        "likely_root_cause": {
            "type": "object",
            "additionalProperties": False,
            "required": ["file", "line", "explanation", "confidence", "evidence"],
            "properties": {
                "file": {"type": "string", "minLength": 1},
                "line": {"type": "integer", "minimum": 1},
                "explanation": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": {"type": "string", "minLength": 3},
            },
        },
        "reproduction_steps": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "recommended_fix": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "file",
                    "line",
                    "severity",
                    "explanation",
                    "recommendation",
                    "confidence",
                    "evidence",
                ],
                "additionalProperties": False,
                "properties": {
                    "file": {"type": "string", "minLength": 1},
                    "line": {"type": "integer", "minimum": 1},
                    "severity": {"enum": ["blocker", "should_fix", "nit"]},
                    "explanation": {"type": "string", "minLength": 1},
                    "recommendation": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string", "minLength": 3},
                },
            },
        },
    },
}


def load_schema() -> dict[str, Any]:
    if SCHEMA_PATH.is_file():
        try:
            data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            # strip $schema meta for our validator
            data.pop("$schema", None)
            data.pop("$id", None)
            data.pop("title", None)
            return data
        except json.JSONDecodeError:
            pass
    return INVESTIGATION_SCHEMA


def validate_investigation_payload(data: dict[str, Any]) -> list[str]:
    """Validate investigation JSON.

    Supports `likely_root_cause: null` even though the shared stdlib validator
    does not understand JSON Schema type unions.
    """
    if not isinstance(data, dict):
        return ["$: expected object"]
    schema = load_schema()
    probe = dict(data)
    if probe.get("likely_root_cause", "__missing__") is None:
        probe.pop("likely_root_cause", None)
        slim = dict(schema)
        slim["required"] = [
            key for key in schema.get("required", []) if key != "likely_root_cause"
        ]
        return _validate(probe, slim)
    return _validate(probe, schema)


def format_investigation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Bug Investigation",
        "",
        f"**Bug:** {payload.get('bug_statement') or '(unspecified)'}",
        "",
        f"**Summary:** {payload.get('summary') or ''}",
        "",
    ]
    root = payload.get("likely_root_cause")
    if isinstance(root, dict):
        lines.append("## Likely root cause")
        lines.append(
            f"- `{root.get('file')}:{root.get('line')}` "
            f"(conf={root.get('confidence')}) — {root.get('explanation')}"
        )
        lines.append(f"  - evidence: `{root.get('evidence')}`")
        lines.append("")
    hyps = payload.get("hypotheses") or []
    if hyps:
        lines.append("## Hypotheses")
        for h in hyps:
            lines.append(
                f"- **{h.get('id')}** [{h.get('status')}/{h.get('likelihood')}] "
                f"{h.get('claim')} (conf={h.get('confidence')})"
            )
            for e in h.get("evidence") or []:
                lines.append(f"  - `{e}`")
        lines.append("")
    steps = payload.get("reproduction_steps") or []
    if steps:
        lines.append("## Reproduction")
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")
    if payload.get("recommended_fix"):
        lines.append("## Recommended fix")
        lines.append(str(payload.get("recommended_fix")))
        lines.append("")
    findings = payload.get("findings") or []
    if findings:
        lines.append("## Findings")
        for f in findings:
            lines.append(
                f"- **{f.get('severity')}** `{f.get('file')}:{f.get('line')}` — "
                f"{f.get('explanation')}"
            )
            lines.append(f"  - fix: {f.get('recommendation')}")
            lines.append(f"  - evidence: `{f.get('evidence')}`")
    else:
        lines.append("## Findings")
        lines.append("- (none)")
    lines.append("")
    lines.append(f"_Analyze notes:_ {payload.get('analyze_notes') or 'none'}")
    return "\n".join(lines)


def read_text_arg(value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return value


def symptom_evidence_ids(bug: str, stacktrace: str) -> set[str]:
    ids = {"symptom:report"}
    blob = f"{bug}\n{stacktrace}".lower()
    for token in ("confirm", "booking", "crash", "null", "loading", "error", "slot"):
        if token in blob:
            ids.add(f"symptom:{token}")
    for path, line in parse_stack_paths(stacktrace):
        ids.add(f"stack:{path}:{line}")
    return ids


def retrieve_for_bug(
    *,
    bug: str,
    stacktrace: str,
    diff: str,
    paths: list[str],
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
        prefer = expand_prefer_paths(paths)
        for path, _ in parse_stack_paths(stacktrace):
            if path not in prefer:
                prefer.append(path)
        prefer = expand_prefer_paths(prefer)

        queries = [
            q
            for q in [
                bug.strip(),
                stacktrace.strip()[:2_000],
                " ".join(paths[:12]),
                (diff or "")[:2_000],
                "Flutter BLoC ScreenState ResponseEntity exception booking",
            ]
            if q and q.strip()
        ]
        print(
            f"Bug RAG: {len(queries)} quer(ies) · prefer {len(prefer)} path(s)...",
            file=sys.stderr,
        )
        all_ranked = []
        for query in queries[:5]:
            embedding = embed_text(query, model=embed_model)
            hits = search(
                embedding,
                top_k=max(top_k, 8),
                db_path=db_path,
                prefer_paths=prefer,
                query_text=query,
                candidate_pool=max(top_k * 8, 32),
            )
            all_ranked.append(hits)

        # Also run PR-style retrieve when a diff exists
        if diff.strip() and paths:
            all_ranked.append(
                advanced_retrieve(
                    diff,
                    paths,
                    db_path=db_path,
                    embed_model=embed_model,
                    top_k=top_k,
                )
            )

        from rag_store import mmr_select, rrf_fuse  # local import after path setup

        fused = rrf_fuse(all_ranked)
        ordered = sorted(fused.values(), key=lambda item: item[0], reverse=True)
        candidates = [hit for _, hit in ordered]
        hits = mmr_select(candidates, top_k=top_k, max_per_path=2)
        return format_hits(hits)
    except RuntimeError as exc:
        return f"(RAG retrieve failed: {exc})"


def build_prompt(
    rules: str,
    *,
    bug: str,
    stacktrace: str,
    diff: str,
    files: str,
    analyze: str,
    retrieved: str,
    scoped_paths: list[str],
    prechecks_text: str,
    mode: str = "full",
    candidate_findings: list[dict[str, Any]] | None = None,
) -> str:
    path_list = "\n".join(f"- {p}" for p in scoped_paths) or "- (repo-wide via RAG)"
    if mode == "strong":
        candidates = json.dumps(candidate_findings or [], indent=2)
        return f"""You are the strong second-pass Bug Investigator for salon_booking.

{assert_read_only_policy()}

Re-evaluate ONLY these serious candidate findings / root-cause claims.
Keep only evidence-backed items. Return the full investigation JSON schema.

# CANDIDATE FINDINGS
{candidates}

# BUG REPORT
{bug or '(none)'}

# STACKTRACE / LOGS
{stacktrace or '(none)'}

# RULES
{rules}

# DETERMINISTIC PRECHECKS
{prechecks_text}

# RETRIEVED CONTEXT
{retrieved}

# FLUTTER ANALYZE
{analyze}

# DIFF
{diff or '(no diff scope)'}

# NEARBY CODE
{files or '(none)'}
"""

    return f"""Investigate this bug for the salon_booking Flutter project.

{assert_read_only_policy()}

Follow RULES exactly. Trace UI → Bloc → Service → Api using RETRIEVED CONTEXT.
Prefer root-cause analysis over style nits.
Scoped paths (when listed, prioritize them):
{path_list}

Output MUST be a single JSON object matching the schema in RULES.

# RULES
{rules}

# BUG REPORT
{bug or '(derive from stacktrace / diff)'}

# STACKTRACE / LOGS
{stacktrace or '(none)'}

# DETERMINISTIC PRECHECKS (leads — do not duplicate blindly)
{prechecks_text}

# RETRIEVED CONTEXT (RAG)
{retrieved}

# FLUTTER ANALYZE
{analyze}

# DIFF (optional scope)
{diff or '(no diff — investigate against retrieved code)'}

# NEARBY CODE
{files or '(none)'}
"""


def apply_output_guardrails(
    raw: str,
    *,
    scoped_paths: list[str],
    min_confidence: float,
    report: GuardrailReport,
    diff_text: str,
    retrieved_text: str,
    analyze_text: str,
    allowed_evidence: set[str],
) -> dict | None:
    data = extract_json_object(raw)
    if data is None:
        report.schema_ok = False
        report.notes.append("Model output was not valid JSON")
        return None

    errors = validate_investigation_payload(data)
    if errors:
        report.schema_ok = False
        report.notes.extend(errors[:20])
        return None

    report.schema_ok = True
    # Do not hard-restrict to scoped_paths — bugs often span UI→Bloc→Service.
    # Evidence grounding still applies.
    filtered, low, invalid, no_evidence = filter_findings(
        data,
        allowed_files=set(),
        min_confidence=min_confidence,
        diff_text=diff_text,
        retrieved_text=retrieved_text,
        analyze_text=analyze_text,
        allowed_evidence=allowed_evidence,
    )
    report.findings_dropped_low_confidence = low
    report.findings_dropped_invalid = invalid
    report.findings_dropped_no_evidence = no_evidence

    # Soft-filter hypotheses with empty evidence
    hyps = []
    for h in filtered.get("hypotheses") or []:
        if not isinstance(h, dict):
            continue
        evid = [e for e in (h.get("evidence") or []) if isinstance(e, str) and e.strip()]
        if not evid:
            continue
        try:
            conf = float(h.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if conf < min_confidence:
            continue
        h = dict(h)
        h["evidence"] = evid
        hyps.append(h)
    filtered["hypotheses"] = hyps

    root = filtered.get("likely_root_cause")
    if isinstance(root, dict):
        try:
            if float(root.get("confidence", 0)) < min_confidence:
                filtered["likely_root_cause"] = None
                report.notes.append("Dropped likely_root_cause below min-confidence")
        except (TypeError, ValueError):
            filtered["likely_root_cause"] = None

    if low or invalid or no_evidence:
        report.notes.append(
            f"Dropped findings: low_confidence={low}, "
            f"invalid_or_out_of_scope={invalid}, no_evidence={no_evidence}"
        )
    return filtered


def merge_findings(
    model_payload: dict[str, Any] | None,
    precheck_findings: list[dict[str, Any]],
    *,
    bug: str,
) -> dict[str, Any]:
    base = {
        "summary": (model_payload or {}).get("summary")
        or "Deterministic bug prechecks only (model produced no valid summary).",
        "bug_statement": (model_payload or {}).get("bug_statement")
        or (bug.strip() or "Unspecified bug — investigate scoped changes."),
        "analyze_notes": (model_payload or {}).get("analyze_notes") or "none",
        "hypotheses": list((model_payload or {}).get("hypotheses") or []),
        "likely_root_cause": (model_payload or {}).get("likely_root_cause"),
        "reproduction_steps": list((model_payload or {}).get("reproduction_steps") or []),
        "recommended_fix": (model_payload or {}).get("recommended_fix") or "",
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local Ollama Bug Investigator — root-cause analysis from symptom/"
            "stacktrace with optional PR/diff scope"
        ),
    )
    parser.add_argument(
        "--bug",
        default="",
        help="Bug symptom description (or path to a text file)",
    )
    parser.add_argument(
        "--stacktrace",
        default="",
        help="Stacktrace/log text (or path to a file)",
    )
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument(
        "--base",
        default=os.environ.get("BUG_INVESTIGATE_BASE")
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
                "BUG_INVESTIGATE_MIN_CONFIDENCE",
                os.environ.get("PR_REVIEW_MIN_CONFIDENCE", str(DEFAULT_MIN_CONFIDENCE)),
            )
        ),
    )
    parser.add_argument("--json-out", default="")
    parser.add_argument("--no-rag", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--dry-gather", action="store_true")
    parser.add_argument(
        "--whole-repo",
        action="store_true",
        help="Run bug-pattern prechecks across lib/ when no PR/diff scope",
    )
    args = parser.parse_args()

    if not RULES_PATH.is_file():
        sys.stderr.write(f"Missing rules file: {RULES_PATH}\n")
        return 1

    bug = read_text_arg(args.bug).strip()
    stacktrace = read_text_arg(args.stacktrace).strip()
    if not bug and not stacktrace and args.pr is None:
        sys.stderr.write(
            "Provide --bug and/or --stacktrace, or --pr to hunt bugs in a PR diff.\n"
        )
        return 1

    report = GuardrailReport()
    rules = RULES_PATH.read_text(encoding="utf-8")
    head = "HEAD"
    base = args.base
    include_dirty = True
    file_ref: str | None = None
    target_label = "bug investigation"
    pr_number = args.pr
    use_diff = False

    if args.pr is not None:
        print(f"Fetching PR #{args.pr} from origin (read-only)...", file=sys.stderr)
        try:
            base, head, pr_label = pr_review.resolve_pr(args.pr, args.base)
        except RuntimeError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        include_dirty = False
        file_ref = head
        use_diff = True
        target_label = f"Bug investigation — {pr_label}"
    elif bug or stacktrace:
        # Optional dirty/branch diff as secondary scope
        use_diff = True
        target_label = "Bug investigation — working tree / branch"

    print(f"Investigating: {target_label}", file=sys.stderr)

    reviewed: list[str] = []
    skipped: list[str] = []
    raw_diff = ""
    guarded_diff = ""
    display_diff = ""
    files = ""

    if use_diff:
        all_paths = pr_review.changed_files(base, head, include_dirty=include_dirty)
        reviewed, skipped = filter_changed_paths(all_paths)
        report.reviewed_paths = reviewed
        report.skipped_paths = skipped
        raw_diff = pr_review.raw_unified_diff(base, head, include_dirty=include_dirty)
        if reviewed:
            guarded_diff = filter_diff_to_paths(raw_diff, set(reviewed))
            display_diff = guarded_diff
            if len(display_diff) > pr_review.MAX_DIFF_CHARS:
                display_diff = (
                    display_diff[: pr_review.MAX_DIFF_CHARS] + "\n\n[diff truncated]"
                )
            loader = lambda p: pr_review.load_file(p, file_ref)  # noqa: E731
            files = build_guarded_file_context(
                reviewed,
                diff_text=raw_diff,
                file_loader=loader,
            )

    # Prefer stack paths + changed paths; else lib/ sample for whole-repo mode
    stack_paths = [p for p, _ in parse_stack_paths(stacktrace)]
    scoped = list(dict.fromkeys(stack_paths + reviewed))
    if not scoped and (args.whole_repo or bug or stacktrace):
        scoped = default_scope_paths(ROOT)[:40]
        report.notes.append(f"Scoped to {len(scoped)} lib/ files for prechecks")
    report.reviewed_paths = scoped

    loader = lambda p: pr_review.load_file(p, file_ref)  # noqa: E731

    print("Running flutter analyze + bug prechecks...", file=sys.stderr)
    analyze = pr_review.flutter_analyze()
    precheck_objs = run_prechecks(
        reviewed_paths=scoped or reviewed,
        diff_text=raw_diff,
        analyze_text=analyze,
        loader=loader,
        root=ROOT,
        stacktrace=stacktrace,
    )
    precheck_findings = [p.as_finding() for p in precheck_objs]
    prechecks_text = format_prechecks_for_prompt(precheck_objs)
    allowed_evidence = precheck_evidence_ids(precheck_objs) | symptom_evidence_ids(
        bug, stacktrace
    )
    print(f"Bug prechecks: {len(precheck_findings)} finding(s)", file=sys.stderr)

    if args.no_rag:
        retrieved = "(RAG disabled)"
    else:
        print("Retrieving RAG context for the bug...", file=sys.stderr)
        retrieved = retrieve_for_bug(
            bug=bug,
            stacktrace=stacktrace,
            diff=guarded_diff,
            paths=scoped or reviewed,
            db_path=Path(args.db),
            embed_model=args.embed_model,
            top_k=args.top_k,
        )

    prompt = build_prompt(
        rules,
        bug=bug,
        stacktrace=stacktrace,
        diff=display_diff,
        files=files,
        analyze=analyze,
        retrieved=retrieved,
        scoped_paths=scoped or reviewed,
        prechecks_text=prechecks_text,
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
    payload = apply_output_guardrails(
        raw_review,
        scoped_paths=scoped or reviewed,
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
                bug=bug,
                stacktrace=stacktrace,
                diff=display_diff,
                files=files,
                analyze=analyze,
                retrieved=retrieved,
                scoped_paths=scoped or reviewed,
                prechecks_text=prechecks_text,
                mode="strong",
                candidate_findings=serious,
            )
            strong_prompt, _ = redact_secrets(strong_prompt)
            raw_strong = pr_review.ollama_chat(
                strong_model,
                strong_prompt,
                system=(
                    "You are the strong second-pass Bug Investigator. "
                    "Emit ONLY valid JSON. Drop weak serious findings. "
                    "Never approve or merge."
                ),
            )
            strong_payload = apply_output_guardrails(
                raw_strong,
                scoped_paths=scoped or reviewed,
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
                    "bug_statement": strong_payload.get("bug_statement")
                    or payload.get("bug_statement"),
                    "analyze_notes": strong_payload.get("analyze_notes")
                    or payload.get("analyze_notes"),
                    "hypotheses": strong_payload.get("hypotheses")
                    or payload.get("hypotheses"),
                    "likely_root_cause": strong_payload.get("likely_root_cause")
                    if strong_payload.get("likely_root_cause") is not None
                    else payload.get("likely_root_cause"),
                    "reproduction_steps": strong_payload.get("reproduction_steps")
                    or payload.get("reproduction_steps"),
                    "recommended_fix": strong_payload.get("recommended_fix")
                    or payload.get("recommended_fix"),
                    "findings": list(strong_payload.get("findings") or []) + nits,
                }
        elif serious and strong_model == triage_model:
            report.notes.append(
                "Strong model unavailable/same as triage — skipped routing"
            )

    merged = merge_findings(payload, precheck_findings, bug=bug)

    mute_rules = load_mute_rules(MUTES_PATH)
    kept, muted = apply_mutes(merged.get("findings") or [], mute_rules)
    report.findings_muted = len(muted)
    if muted:
        report.notes.append(
            "Muted: " + ", ".join(sorted({m.get("muted_by", "?") for m in muted}))
        )
    merged["findings"] = kept

    markdown = format_investigation_markdown(merged)
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
                bug=bug or stacktrace[:200],
                triage_model=triage_model,
                strong_model=strong_model,
                routing_used=routing_used,
                reviewed_paths=scoped or reviewed,
                skipped_paths=skipped,
                payload=merged,
                precheck_count=len(precheck_findings),
                muted_count=len(muted),
                guardrail_notes=report.notes
                + [
                    f"routing_used={routing_used}",
                    f"prechecks={len(precheck_findings)}",
                    "agent=bug-investigator",
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
