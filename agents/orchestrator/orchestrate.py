#!/usr/bin/env python3
"""Local Orchestrator: fan-out to PR / security / performance / bug agents."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parents[1]
sys.path.insert(0, str(AGENT_DIR))

from report import build_report_payload, write_report  # noqa: E402

RULES_PATH = AGENT_DIR / "RULES.md"
DEFAULT_TRIAGE_MODEL = os.environ.get("OLLAMA_TRIAGE_MODEL") or os.environ.get(
    "OLLAMA_MODEL", "llama3.2"
)
DEFAULT_STRONG_MODEL = os.environ.get("OLLAMA_STRONG_MODEL", DEFAULT_TRIAGE_MODEL)
DEFAULT_BASE = os.environ.get("ORCHESTRATE_BASE") or os.environ.get(
    "PR_REVIEW_BASE", "origin/main"
)
DEFAULT_AGENTS = ("pr", "security", "performance")

AGENT_SPECS: dict[str, dict[str, Any]] = {
    "pr": {
        "label": "PR reviewer",
        "script": ROOT / "scripts" / "pr-review.sh",
        "python": ROOT / "agents" / "pr-reviewer" / "review.py",
        "reports": ROOT / "agents" / "pr-reviewer" / "reports",
        "dir_name": "pr-reviewer",
    },
    "security": {
        "label": "Security reviewer",
        "script": ROOT / "scripts" / "sec-review.sh",
        "python": ROOT / "agents" / "security-reviewer" / "review.py",
        "reports": ROOT / "agents" / "security-reviewer" / "reports",
        "dir_name": "security-reviewer",
    },
    "performance": {
        "label": "Performance reviewer",
        "script": ROOT / "scripts" / "perf-review.sh",
        "python": ROOT / "agents" / "performance-reviewer" / "review.py",
        "reports": ROOT / "agents" / "performance-reviewer" / "reports",
        "dir_name": "performance-reviewer",
    },
    "bug": {
        "label": "Bug investigator",
        "script": ROOT / "scripts" / "bug-investigate.sh",
        "python": ROOT / "agents" / "bug-investigator" / "investigate.py",
        "reports": ROOT / "agents" / "bug-investigator" / "reports",
        "dir_name": "bug-investigator",
    },
}


def _parse_agents(raw: str | None, *, include_bug: bool) -> list[str]:
    if raw:
        agents = [a.strip().lower() for a in raw.split(",") if a.strip()]
    else:
        agents = list(DEFAULT_AGENTS)
        if include_bug and "bug" not in agents:
            agents.append("bug")
    unknown = [a for a in agents if a not in AGENT_SPECS]
    if unknown:
        raise SystemExit(
            f"Unknown agent(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(AGENT_SPECS)}"
        )
    # stable unique order
    seen: set[str] = set()
    ordered: list[str] = []
    for a in agents:
        if a not in seen:
            seen.add(a)
            ordered.append(a)
    return ordered


def _forward_args(args: argparse.Namespace, agent: str) -> list[str]:
    out: list[str] = ["--no-open"]
    if args.pr is not None:
        out.extend(["--pr", str(args.pr)])
    if args.base:
        out.extend(["--base", args.base])
    if args.model:
        out.extend(["--model", args.model])
    if args.strong_model:
        out.extend(["--strong-model", args.strong_model])
    if args.no_routing:
        out.append("--no-routing")
    if args.embed_model:
        out.extend(["--embed-model", args.embed_model])
    if args.db:
        out.extend(["--db", args.db])
    if args.top_k is not None:
        out.extend(["--top-k", str(args.top_k)])
    if args.min_confidence is not None:
        out.extend(["--min-confidence", str(args.min_confidence)])
    if args.no_rag:
        out.append("--no-rag")
    if args.dry_gather:
        out.append("--dry-gather")
    if args.no_report:
        out.append("--no-report")

    if agent == "bug":
        if args.bug:
            out.extend(["--bug", args.bug])
        if args.stacktrace:
            out.extend(["--stacktrace", args.stacktrace])
        if args.whole_repo:
            out.append("--whole-repo")
    return out


def _run_agent(agent: str, args: argparse.Namespace) -> dict[str, Any]:
    spec = AGENT_SPECS[agent]
    cmd = ["python3", str(spec["python"]), *_forward_args(args, agent)]
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            check=False,
            text=True,
            capture_output=False,
        )
        exit_code = int(proc.returncode)
        status = "ok" if exit_code == 0 else "failed"
        error = ""
    except OSError as exc:
        exit_code = 127
        status = "failed"
        error = str(exc)

    duration_ms = int((time.perf_counter() - started) * 1000)
    report_path = spec["reports"] / "latest" / "report.json"
    child_summary = ""
    child_payload: dict[str, Any] | None = None
    if report_path.is_file() and not args.no_report and not args.dry_gather:
        try:
            child_payload = json.loads(report_path.read_text(encoding="utf-8"))
            child_summary = str(child_payload.get("summary") or "")
        except json.JSONDecodeError as exc:
            error = (error + "; " if error else "") + f"invalid report JSON: {exc}"
            if status == "ok":
                status = "failed"
                exit_code = exit_code or 1

    return {
        "agent": agent,
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "report_path": str(report_path.relative_to(ROOT)) if report_path.is_file() else None,
        "error": error,
        "child_summary": child_summary,
        "_payload": child_payload,
    }


def _flatten_findings(agent: str, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    out: list[dict[str, Any]] = []
    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        item = dict(finding)
        item["agent"] = agent
        out.append(item)
    return out


def _bug_investigation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    keys = (
        "bug_statement",
        "hypotheses",
        "likely_root_cause",
        "reproduction_steps",
        "recommended_fix",
        "summary",
    )
    if not any(payload.get(k) for k in keys):
        return None
    return {k: payload.get(k) for k in keys if payload.get(k) is not None}


def _build_summary(runs: list[dict[str, Any]], findings: list[dict[str, Any]]) -> str:
    ok = [r["agent"] for r in runs if r.get("status") == "ok"]
    failed = [r["agent"] for r in runs if r.get("status") == "failed"]
    blockers = sum(1 for f in findings if f.get("severity") == "blocker")
    should = sum(1 for f in findings if f.get("severity") == "should_fix")
    nits = sum(1 for f in findings if f.get("severity") == "nit")
    parts = [
        f"{len(ok)} agent(s) ok"
        + (f", {len(failed)} failed ({', '.join(failed)})" if failed else ""),
        f"{blockers} blocker(s), {should} should_fix, {nits} nit(s)",
    ]
    snippets = [
        f"{r['agent']}: {r['child_summary']}"
        for r in runs
        if r.get("status") == "ok" and r.get("child_summary")
    ]
    if snippets:
        parts.append(" | ".join(snippets[:4]))
    return ". ".join(parts) + "."


def _print_markdown(payload: dict[str, Any]) -> None:
    print("# Orchestrator summary\n")
    print(payload.get("summary", ""))
    print("\n## Agent runs\n")
    for run in payload.get("agent_runs") or []:
        note = run.get("child_summary") or run.get("error") or ""
        print(
            f"- **{run.get('agent')}** — {run.get('status')} "
            f"(exit {run.get('exit_code')}, {run.get('duration_ms')} ms)"
            + (f": {note}" if note else "")
        )
    findings = payload.get("findings") or []
    if findings:
        print("\n## Findings\n")
        for f in findings:
            loc = f.get("file") or ""
            if f.get("line"):
                loc = f"{loc}:{f.get('line')}"
            print(
                f"- [{f.get('severity')}] ({f.get('agent')}) {loc} — "
                f"{f.get('explanation')}"
            )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local Orchestrator — fan-out to PR / security / performance / "
            "bug agents and aggregate reports"
        ),
    )
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument(
        "--agents",
        default="",
        help="Comma-separated: pr,security,performance,bug "
        f"(default: {','.join(DEFAULT_AGENTS)}; bug auto-added with --bug/--stacktrace)",
    )
    parser.add_argument("--parallel", action="store_true", help="Run agents concurrently")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop scheduling remaining agents after the first failure (sequential only)",
    )
    parser.add_argument("--bug", default="", help="Forwarded to bug investigator")
    parser.add_argument("--stacktrace", default="", help="Forwarded to bug investigator")
    parser.add_argument(
        "--whole-repo",
        action="store_true",
        help="Forwarded to bug investigator",
    )
    parser.add_argument("--model", default=DEFAULT_TRIAGE_MODEL)
    parser.add_argument("--strong-model", default=DEFAULT_STRONG_MODEL)
    parser.add_argument("--no-routing", action="store_true")
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / "agents" / "pr-reviewer" / "index" / "rag.sqlite"),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=int(os.environ.get("ORCHESTRATE_TOP_K") or os.environ.get("PR_REVIEW_TOP_K", "8")),
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=float(
            os.environ.get(
                "ORCHESTRATE_MIN_CONFIDENCE",
                os.environ.get("PR_REVIEW_MIN_CONFIDENCE", "0.55"),
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

    include_bug = bool(args.bug.strip() or args.stacktrace.strip())
    agents = _parse_agents(args.agents or None, include_bug=include_bug)
    if "bug" in agents and not include_bug and args.pr is None and not args.whole_repo:
        sys.stderr.write(
            "Bug investigator selected but missing --bug/--stacktrace "
            "(or --pr / --whole-repo).\n"
        )
        return 1

    if args.fail_fast and args.parallel:
        sys.stderr.write("Note: --fail-fast is ignored with --parallel.\n")

    print(
        f"Orchestrator → agents=[{', '.join(agents)}] "
        f"parallel={args.parallel} base={args.base}"
        + (f" pr=#{args.pr}" if args.pr is not None else ""),
        file=sys.stderr,
    )

    runs: list[dict[str, Any]] = []
    if args.parallel and len(agents) > 1:
        with ThreadPoolExecutor(max_workers=len(agents)) as pool:
            futures = {pool.submit(_run_agent, agent, args): agent for agent in agents}
            results: dict[str, dict[str, Any]] = {}
            for fut in as_completed(futures):
                agent = futures[fut]
                try:
                    results[agent] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    results[agent] = {
                        "agent": agent,
                        "status": "failed",
                        "exit_code": 1,
                        "duration_ms": 0,
                        "report_path": None,
                        "error": str(exc),
                        "child_summary": "",
                        "_payload": None,
                    }
            runs = [results[a] for a in agents]
    else:
        for agent in agents:
            run = _run_agent(agent, args)
            runs.append(run)
            if args.fail_fast and run.get("status") == "failed":
                for skipped in agents[agents.index(agent) + 1 :]:
                    runs.append(
                        {
                            "agent": skipped,
                            "status": "skipped",
                            "exit_code": -1,
                            "duration_ms": 0,
                            "report_path": None,
                            "error": "skipped due to --fail-fast",
                            "child_summary": "",
                            "_payload": None,
                        }
                    )
                break

    findings: list[dict[str, Any]] = []
    bug_investigations: list[dict[str, Any]] = []
    child_links: list[dict[str, str]] = []
    for run in runs:
        agent = str(run["agent"])
        payload = run.pop("_payload", None)
        findings.extend(_flatten_findings(agent, payload))
        if agent == "bug":
            inv = _bug_investigation(payload)
            if inv:
                bug_investigations.append(inv)
        if run.get("report_path"):
            # Relative from agents/orchestrator/reports/<run>/index.html
            html_rel = (
                Path("..")
                / ".."
                / ".."
                / AGENT_SPECS[agent]["dir_name"]
                / "reports"
                / "latest"
                / "index.html"
            )
            child_links.append(
                {
                    "label": f"{AGENT_SPECS[agent]['label']} report",
                    "href": str(html_rel),
                }
            )

    # Sort findings: severity then agent
    sev_rank = {"blocker": 0, "should_fix": 1, "nit": 2}
    findings.sort(
        key=lambda f: (
            sev_rank.get(str(f.get("severity")), 9),
            str(f.get("agent") or ""),
            str(f.get("file") or ""),
        )
    )

    public_runs = [
        {k: v for k, v in run.items() if not k.startswith("_")} for run in runs
    ]
    failed_agents = sum(1 for r in public_runs if r.get("status") == "failed")
    orchestration = {
        "summary": _build_summary(public_runs, findings),
        "agent_runs": public_runs,
        "findings": findings,
        "bug_investigations": bug_investigations,
        "counts": {
            "blocker": sum(1 for f in findings if f.get("severity") == "blocker"),
            "should_fix": sum(1 for f in findings if f.get("severity") == "should_fix"),
            "nit": sum(1 for f in findings if f.get("severity") == "nit"),
            "total": len(findings),
            "failed_agents": failed_agents,
        },
        "read_only": True,
    }

    if not args.dry_gather:
        _print_markdown(orchestration)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(orchestration, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote orchestration JSON → {out_path}", file=sys.stderr)

    if not args.no_report and not args.dry_gather:
        label = f"PR #{args.pr}" if args.pr is not None else "local working tree"
        html_path = write_report(
            build_report_payload(
                label=label,
                pr=args.pr,
                base=args.base,
                agents=agents,
                parallel=args.parallel,
                payload=orchestration,
                child_report_links=child_links,
            ),
            open_browser=not args.no_open,
        )
        print(f"Orchestrator report → {html_path}", file=sys.stderr)

    if failed_agents:
        return 1
    if any(r.get("exit_code", 0) not in (0, -1) for r in public_runs):
        return max(int(r.get("exit_code") or 0) for r in public_runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
