#!/usr/bin/env python3
"""HTML/JSON report pack for orchestrated multi-agent reviews."""

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

SEVERITY_ORDER = {"blocker": 0, "should_fix": 1, "nit": 2}


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _count(findings: list[dict[str, Any]], severity: str) -> int:
    return sum(1 for f in findings if f.get("severity") == severity)


def build_report_payload(
    *,
    label: str,
    pr: int | None,
    base: str,
    agents: list[str],
    parallel: bool,
    payload: dict[str, Any],
    child_report_links: list[dict[str, str]],
) -> dict[str, Any]:
    findings = list(payload.get("findings") or [])
    return {
        "agent": "orchestrator",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "pr": pr,
        "base": base,
        "agents": agents,
        "parallel": parallel,
        "summary": payload.get("summary", ""),
        "agent_runs": payload.get("agent_runs") or [],
        "findings": findings,
        "bug_investigations": payload.get("bug_investigations") or [],
        "counts": payload.get("counts")
        or {
            "blocker": _count(findings, "blocker"),
            "should_fix": _count(findings, "should_fix"),
            "nit": _count(findings, "nit"),
            "total": len(findings),
            "failed_agents": 0,
        },
        "child_report_links": child_report_links,
        "read_only": True,
    }


def _finding_cards(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return '<p class="muted">No findings across agents.</p>'
    ordered = sorted(
        findings,
        key=lambda f: (
            SEVERITY_ORDER.get(str(f.get("severity")), 9),
            str(f.get("agent") or ""),
            str(f.get("file") or ""),
        ),
    )
    cards: list[str] = []
    for f in ordered:
        sev = _esc(f.get("severity"))
        agent = _esc(f.get("agent"))
        loc = _esc(f.get("file") or "")
        if f.get("line"):
            loc = f"{loc}:{_esc(f.get('line'))}"
        source = _esc(f.get("source") or "model")
        cards.append(
            f"""
            <article class="card sev-{sev}">
              <header>
                <span class="pill {sev}">{sev}</span>
                <span class="pill agent">{agent}</span>
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


def _run_rows(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "<tr><td colspan='5' class='muted'>No agent runs.</td></tr>"
    rows: list[str] = []
    for r in runs:
        status = _esc(r.get("status"))
        rows.append(
            f"""
            <tr class="status-{status}">
              <td><code>{_esc(r.get('agent'))}</code></td>
              <td>{status}</td>
              <td>{_esc(r.get('exit_code'))}</td>
              <td>{_esc(r.get('duration_ms'))} ms</td>
              <td>{_esc(r.get('child_summary') or r.get('error') or '')}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def _child_links(links: list[dict[str, str]]) -> str:
    if not links:
        return "<p class='muted'>No child reports.</p>"
    items = [
        f'<li><a href="{_esc(link.get("href"))}">{_esc(link.get("label"))}</a></li>'
        for link in links
    ]
    return "<ul>" + "\n".join(items) + "</ul>"


def _render_html(report: dict[str, Any]) -> str:
    findings = report.get("findings") or []
    counts = report.get("counts") or {}
    runs = report.get("agent_runs") or []
    links = report.get("child_report_links") or []
    bugs = report.get("bug_investigations") or []
    bug_html = ""
    if bugs:
        parts = []
        for b in bugs:
            parts.append(
                f"""
                <article class="card">
                  <header><span class="pill agent">bug</span></header>
                  <p><strong>{_esc(b.get('likely_root_cause') or b.get('bug_statement') or 'Investigation')}</strong></p>
                  <p class="muted">{_esc(b.get('recommended_fix') or '')}</p>
                </article>
                """
            )
        bug_html = "<h2>Bug investigations</h2>" + "\n".join(parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Orchestrator — {_esc(report.get('label'))}</title>
  <style>
    :root {{
      --bg: #f4f6f5;
      --ink: #14211c;
      --muted: #6b7280;
      --line: #dce3df;
      --blocker: #b91c1c;
      --should: #c2410c;
      --nit: #0f766e;
      --card: #ffffff;
      --agent: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #d9eee4, transparent),
                  linear-gradient(180deg, #eef3f0, var(--bg));
      color: var(--ink);
      line-height: 1.45;
    }}
    main {{ max-width: 980px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
    h1 {{ font-size: 1.6rem; margin: 0 0 0.35rem; }}
    h2 {{ font-size: 1.1rem; margin: 2rem 0 0.75rem; }}
    .muted {{ color: var(--muted); }}
    .stats {{ display: flex; flex-wrap: wrap; gap: 0.6rem; margin: 1rem 0 1.5rem; }}
    .stat {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.65rem 0.9rem;
      min-width: 7rem;
    }}
    .stat b {{ display: block; font-size: 1.25rem; }}
    .pill {{
      display: inline-block;
      border-radius: 999px;
      padding: 0.1rem 0.55rem;
      font-size: 0.75rem;
      font-weight: 600;
      color: #fff;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .pill.blocker {{ background: var(--blocker); }}
    .pill.should_fix {{ background: var(--should); }}
    .pill.nit {{ background: var(--nit); }}
    .pill.agent {{ background: var(--agent); }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 0.9rem 1rem;
      margin: 0.65rem 0;
    }}
    .card.sev-blocker {{ border-left: 4px solid var(--blocker); }}
    .card.sev-should_fix {{ border-left: 4px solid var(--should); }}
    .card.sev-nit {{ border-left: 4px solid var(--nit); }}
    .card header {{ display: flex; flex-wrap: wrap; gap: 0.45rem; align-items: center; margin-bottom: 0.4rem; }}
    .evidence code {{ font-size: 0.82rem; word-break: break-all; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--card); border-radius: 12px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 0.55rem 0.7rem; border-bottom: 1px solid var(--line); font-size: 0.92rem; vertical-align: top; }}
    th {{ background: #e8efeb; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
    tr.status-failed td {{ color: var(--blocker); }}
    tr.status-skipped td {{ color: var(--muted); }}
    a {{ color: var(--agent); }}
  </style>
</head>
<body>
<main>
  <h1>Orchestrator report</h1>
  <p class="muted">{_esc(report.get('label'))} · {_esc(report.get('generated_at'))}</p>
  <p>{_esc(report.get('summary'))}</p>
  <div class="stats">
    <div class="stat"><b>{_esc(counts.get('blocker', 0))}</b>blockers</div>
    <div class="stat"><b>{_esc(counts.get('should_fix', 0))}</b>should fix</div>
    <div class="stat"><b>{_esc(counts.get('nit', 0))}</b>nits</div>
    <div class="stat"><b>{_esc(counts.get('total', 0))}</b>findings</div>
    <div class="stat"><b>{_esc(counts.get('failed_agents', 0))}</b>failed agents</div>
  </div>

  <h2>Agent runs</h2>
  <table>
    <thead>
      <tr><th>Agent</th><th>Status</th><th>Exit</th><th>Duration</th><th>Notes</th></tr>
    </thead>
    <tbody>
      {_run_rows(runs)}
    </tbody>
  </table>

  <h2>Child reports</h2>
  {_child_links(links)}

  {bug_html}

  <h2>Findings</h2>
  {_finding_cards(findings)}
</main>
</body>
</html>
"""


def _dashboard_html(entries: list[dict[str, Any]]) -> str:
    body = ""
    if not entries:
        body = "<tr><td colspan='5' class='muted'>No orchestration runs yet.</td></tr>"
    else:
        rows = []
        for e in entries:
            rows.append(
                f"""
                <tr>
                  <td><a href="{_esc(e.get('href'))}">{_esc(e.get('label'))}</a></td>
                  <td>{_esc(e.get('blocker', 0))}</td>
                  <td>{_esc(e.get('should_fix', 0))}</td>
                  <td>{_esc(e.get('nit', 0))}</td>
                  <td class="muted">{_esc(e.get('generated_at'))}</td>
                </tr>
                """
            )
        body = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/><title>Orchestrator reports</title>
<style>
  body {{ font-family: "IBM Plex Sans", sans-serif; margin: 2rem; background: #f4f6f5; color: #14211c; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; }}
  th, td {{ border-bottom: 1px solid #dce3df; padding: 0.55rem 0.7rem; text-align: left; }}
  th {{ color: #6b7280; font-size: 0.78rem; text-transform: uppercase; }}
  .muted {{ color: #6b7280; }}
  a {{ color: #1d4ed8; }}
</style>
</head><body>
<h1>Orchestrator reports</h1>
<table>
<thead><tr><th>Run</th><th>Blockers</th><th>Should fix</th><th>Nits</th><th>Generated</th></tr></thead>
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
                "nit": counts.get("nit", 0),
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
