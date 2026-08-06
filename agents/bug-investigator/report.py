#!/usr/bin/env python3
"""HTML/JSON report pack for local bug investigations."""

from __future__ import annotations

import html
import json
import shutil
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = AGENT_DIR / "reports"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _count(findings: list[dict[str, Any]], severity: str) -> int:
    return sum(1 for f in findings if f.get("severity") == severity)


def build_report_payload(
    *,
    label: str,
    pr: int | None,
    base: str,
    head: str,
    bug: str,
    triage_model: str,
    strong_model: str,
    routing_used: bool,
    reviewed_paths: list[str],
    skipped_paths: list[str],
    payload: dict[str, Any],
    precheck_count: int,
    muted_count: int,
    guardrail_notes: list[str],
) -> dict[str, Any]:
    findings = list(payload.get("findings") or [])
    return {
        "agent": "bug-investigator",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "pr": pr,
        "base": base,
        "head": head,
        "bug": bug,
        "triage_model": triage_model,
        "strong_model": strong_model,
        "routing_used": routing_used,
        "reviewed_paths": reviewed_paths,
        "skipped_paths": skipped_paths,
        "summary": payload.get("summary", ""),
        "bug_statement": payload.get("bug_statement", ""),
        "analyze_notes": payload.get("analyze_notes", ""),
        "hypotheses": payload.get("hypotheses") or [],
        "likely_root_cause": payload.get("likely_root_cause"),
        "reproduction_steps": payload.get("reproduction_steps") or [],
        "recommended_fix": payload.get("recommended_fix", ""),
        "findings": findings,
        "counts": {
            "blocker": _count(findings, "blocker"),
            "should_fix": _count(findings, "should_fix"),
            "nit": _count(findings, "nit"),
            "hypotheses": len(payload.get("hypotheses") or []),
            "precheck": precheck_count,
            "muted": muted_count,
            "total": len(findings),
        },
        "guardrail_notes": guardrail_notes,
        "read_only": True,
    }


def _finding_cards(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return '<p class="muted">No actionable findings.</p>'
    cards: list[str] = []
    for f in findings:
        sev = _esc(f.get("severity"))
        loc = _esc(f.get("file"))
        if f.get("line"):
            loc = f"{loc}:{_esc(f.get('line'))}"
        source = _esc(f.get("source") or "model")
        cards.append(
            f"""
            <article class="card sev-{sev}">
              <header>
                <span class="pill {sev}">{sev}</span>
                <code>{loc}</code>
                <span class="muted">{source} · conf={_esc(f.get('confidence'))}</span>
              </header>
              <p><strong>{_esc(f.get('explanation'))}</strong></p>
              <p class="muted">Recommendation: {_esc(f.get('recommendation'))}</p>
              <p class="evidence">Evidence: <code>{_esc(f.get('evidence'))}</code></p>
            </article>
            """
        )
    return "\n".join(cards)


def _hypothesis_cards(hypotheses: list[dict[str, Any]]) -> str:
    if not hypotheses:
        return '<p class="muted">No hypotheses.</p>'
    cards: list[str] = []
    for h in hypotheses:
        evid = h.get("evidence") or []
        evid_html = "".join(f"<li><code>{_esc(e)}</code></li>" for e in evid)
        cards.append(
            f"""
            <article class="card hyp-{_esc(h.get('status'))}">
              <header>
                <span class="pill status">{_esc(h.get('id'))} · {_esc(h.get('status'))}</span>
                <span class="muted">{_esc(h.get('likelihood'))} · conf={_esc(h.get('confidence'))}</span>
              </header>
              <p><strong>{_esc(h.get('claim'))}</strong></p>
              <ul class="evidence-list">{evid_html}</ul>
            </article>
            """
        )
    return "\n".join(cards)


def _render_html(report: dict[str, Any]) -> str:
    findings = report.get("findings") or []
    hypotheses = report.get("hypotheses") or []
    counts = report.get("counts") or {}
    notes = report.get("guardrail_notes") or []
    paths = report.get("reviewed_paths") or []
    root = report.get("likely_root_cause")
    steps = report.get("reproduction_steps") or []
    root_html = '<p class="muted">Insufficient evidence for a single root cause.</p>'
    if isinstance(root, dict):
        root_html = f"""
        <article class="card sev-should_fix">
          <header>
            <span class="pill should_fix">root cause</span>
            <code>{_esc(root.get('file'))}:{_esc(root.get('line'))}</code>
            <span class="muted">conf={_esc(root.get('confidence'))}</span>
          </header>
          <p><strong>{_esc(root.get('explanation'))}</strong></p>
          <p class="evidence">Evidence: <code>{_esc(root.get('evidence'))}</code></p>
        </article>
        """
    steps_html = (
        "<ol>" + "".join(f"<li>{_esc(s)}</li>" for s in steps) + "</ol>"
        if steps
        else '<p class="muted">none</p>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Bug Investigation — {_esc(report.get('label'))}</title>
  <style>
    :root {{
      --bg: #f7f4f1;
      --ink: #1f1612;
      --muted: #7a6a60;
      --line: #e8ddd4;
      --blocker: #b91c1c;
      --should: #c2410c;
      --nit: #1d4ed8;
      --card: #fffdfb;
    }}
    body {{
      margin: 0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 8% 0%, #fdba7455, transparent 40%),
        linear-gradient(180deg, #fbf7f3, var(--bg));
      color: var(--ink);
    }}
    main {{ max-width: 980px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
    h1 {{ font-family: "Iowan Old Style", "Palatino Linotype", serif;
         font-size: 2rem; margin: 0 0 .35rem; }}
    h2 {{ margin-top: 2rem; font-size: 1.15rem; }}
    .muted {{ color: var(--muted); }}
    .strip {{ display: flex; flex-wrap: wrap; gap: .6rem; margin: 1rem 0 1.5rem; }}
    .stat {{ background: var(--card); border: 1px solid var(--line);
            border-radius: 12px; padding: .75rem 1rem; min-width: 7rem; }}
    .stat b {{ display: block; font-size: 1.4rem; }}
    .pill {{ display: inline-block; border-radius: 999px; padding: .15rem .55rem;
            font-size: .75rem; color: #fff; text-transform: uppercase; }}
    .pill.blocker {{ background: var(--blocker); }}
    .pill.should_fix {{ background: var(--should); }}
    .pill.nit {{ background: var(--nit); }}
    .pill.status {{ background: #44403c; }}
    .card {{ background: var(--card); border: 1px solid var(--line);
            border-radius: 14px; padding: 1rem 1.1rem; margin: .75rem 0; }}
    .card.sev-blocker {{ border-left: 4px solid var(--blocker); }}
    .card.sev-should_fix {{ border-left: 4px solid var(--should); }}
    .card.sev-nit {{ border-left: 4px solid var(--nit); }}
    .card.hyp-likely {{ border-left: 4px solid var(--should); }}
    .card.hyp-possible {{ border-left: 4px solid var(--nit); }}
    .card.hyp-ruled_out {{ border-left: 4px solid #a8a29e; opacity: .85; }}
    .card header {{ display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
                   margin-bottom: .4rem; }}
    code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .86em; }}
    .evidence {{ font-size: .9rem; }}
    .evidence-list {{ margin: .4rem 0 0; padding-left: 1.1rem; }}
    ul.paths {{ columns: 2; gap: 1.5rem; }}
    footer {{ margin-top: 2.5rem; color: var(--muted); font-size: .85rem; }}
    .bugbox {{ background: var(--card); border: 1px dashed var(--line);
              border-radius: 12px; padding: 1rem; }}
  </style>
</head>
<body>
<main>
  <p class="muted">Local Bug Investigator · root-cause analysis · read-only</p>
  <h1>{_esc(report.get('label'))}</h1>
  <p class="muted">
    {_esc(report.get('base'))}…{_esc(report.get('head'))}<br/>
    Triage: <code>{_esc(report.get('triage_model'))}</code>
    · Strong: <code>{_esc(report.get('strong_model'))}</code>
    · Routing: {_esc('yes' if report.get('routing_used') else 'no')}<br/>
    Generated {_esc(report.get('generated_at'))}
  </p>

  <section class="strip">
    <div class="stat"><b>{counts.get('blocker', 0)}</b>blockers</div>
    <div class="stat"><b>{counts.get('should_fix', 0)}</b>should fix</div>
    <div class="stat"><b>{counts.get('nit', 0)}</b>nits</div>
    <div class="stat"><b>{counts.get('hypotheses', 0)}</b>hypotheses</div>
    <div class="stat"><b>{counts.get('precheck', 0)}</b>prechecks</div>
  </section>

  <h2>Bug statement</h2>
  <div class="bugbox">{_esc(report.get('bug_statement') or report.get('bug'))}</div>

  <h2>Summary</h2>
  <p>{_esc(report.get('summary'))}</p>

  <h2>Likely root cause</h2>
  {root_html}

  <h2>Hypotheses</h2>
  {_hypothesis_cards(hypotheses)}

  <h2>Reproduction steps</h2>
  {steps_html}

  <h2>Recommended fix</h2>
  <p>{_esc(report.get('recommended_fix') or 'none')}</p>

  <h2>Findings board</h2>
  {_finding_cards(findings)}

  <h2>Analyze notes</h2>
  <p>{_esc(report.get('analyze_notes') or 'none')}</p>

  <h2>Scoped files</h2>
  <ul class="paths">
    {''.join(f'<li><code>{_esc(p)}</code></li>' for p in paths) or '<li class="muted">(none)</li>'}
  </ul>

  <h2>Guardrail notes</h2>
  <ul>
    {''.join(f'<li>{_esc(n)}</li>' for n in notes) or '<li class="muted">none</li>'}
  </ul>

  <footer>
    Artifact: <code>report.json</code> + this HTML.
    Edit <code>mutes.yaml</code> to suppress recurring noise.
    Reuses PR reviewer RAG index at <code>agents/pr-reviewer/index/rag.sqlite</code>.
  </footer>
</main>
</body>
</html>
"""


def _dashboard_html(entries: list[dict[str, Any]]) -> str:
    rows = []
    for e in entries:
        rows.append(
            f"<tr>"
            f"<td><a href='{_esc(e['href'])}'>{_esc(e['label'])}</a></td>"
            f"<td>{e['blocker']}</td><td>{e['should_fix']}</td><td>{e['hypotheses']}</td>"
            f"<td class='muted'>{_esc(e['generated_at'])}</td>"
            f"</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='5'>No reports yet.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Bug Investigation Dashboard</title>
<style>
 body {{ font-family: "IBM Plex Sans", sans-serif; margin: 2rem; background: #f7f4f1; }}
 table {{ border-collapse: collapse; width: 100%; background: #fffdfb; }}
 th, td {{ border-bottom: 1px solid #e8ddd4; padding: .65rem .75rem; text-align: left; }}
 .muted {{ color: #7a6a60; }}
</style></head>
<body>
<h1>Bug Investigation Dashboard</h1>
<p class="muted">Local reports · root-cause analysis · read-only agent</p>
<table>
<thead><tr><th>Investigation</th><th>Blockers</th><th>Should fix</th><th>Hypotheses</th><th>When</th></tr></thead>
<tbody>{body}</tbody>
</table>
</body></html>
"""


def write_report(
    report: dict[str, Any],
    *,
    open_browser: bool = True,
) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pr = report.get("pr")
    folder_name = f"pr-{pr}" if pr is not None else "local"
    out_dir = REPORTS_DIR / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "report.json"
    html_path = out_dir / "index.html"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(report), encoding="utf-8")

    latest = REPORTS_DIR / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(out_dir, latest)

    entries: list[dict[str, Any]] = []
    for child in sorted(REPORTS_DIR.iterdir()):
        if not child.is_dir() or child.name == "latest":
            continue
        candidate = child / "report.json"
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        counts = data.get("counts") or {}
        entries.append(
            {
                "label": data.get("label") or child.name,
                "href": f"{child.name}/index.html",
                "blocker": counts.get("blocker", 0),
                "should_fix": counts.get("should_fix", 0),
                "hypotheses": counts.get("hypotheses", 0),
                "generated_at": data.get("generated_at", ""),
            }
        )
    (REPORTS_DIR / "index.html").write_text(
        _dashboard_html(entries),
        encoding="utf-8",
    )

    if open_browser:
        try:
            webbrowser.open(html_path.resolve().as_uri())
        except Exception:  # noqa: BLE001
            pass
    return html_path
