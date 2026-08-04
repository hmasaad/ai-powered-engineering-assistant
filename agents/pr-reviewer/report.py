#!/usr/bin/env python3
"""Generate HTML PR review reports (coverage-style local artifact)."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def extract_json_payload(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object from model output, if present."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def parse_markdown_review(text: str) -> dict[str, Any]:
    """Fallback parser for markdown section output."""
    sections = {
        "summary": "",
        "blockers": [],
        "should_fix": [],
        "nits": [],
        "analyze": "",
    }
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current
        if current is None:
            buffer = []
            return
        body = "\n".join(buffer).strip()
        if current == "summary":
            sections["summary"] = body
        elif current == "analyze":
            sections["analyze"] = body
        elif current in {"blockers", "should_fix", "nits"}:
            items = []
            for line in body.splitlines():
                line = line.strip()
                if not line or line.lower() == "none":
                    continue
                line = re.sub(r"^[-*]\s*", "", line)
                file_match = re.match(r"`([^`]+)`\s*:?\s*(.*)$", line)
                if file_match:
                    items.append(
                        {
                            "file": file_match.group(1),
                            "title": file_match.group(1),
                            "detail": file_match.group(2) or line,
                        }
                    )
                else:
                    items.append(
                        {
                            "file": "",
                            "title": line[:80],
                            "detail": line,
                        }
                    )
            sections[current] = items
        buffer = []

    for line in text.splitlines():
        heading = re.match(r"^##\s+(.*)$", line.strip())
        if heading:
            flush()
            name = heading.group(1).strip().lower()
            if name.startswith("summary"):
                current = "summary"
            elif name.startswith("blocker"):
                current = "blockers"
            elif name.startswith("should"):
                current = "should_fix"
            elif name.startswith("nit"):
                current = "nits"
            elif name.startswith("analyze"):
                current = "analyze"
            else:
                current = None
            continue
        if current is not None:
            buffer.append(line)
    flush()

    findings: list[dict[str, Any]] = []
    for severity, key in (
        ("blocker", "blockers"),
        ("should_fix", "should_fix"),
        ("nit", "nits"),
    ):
        for item in sections[key]:
            findings.append(
                {
                    "severity": severity,
                    "file": item.get("file", ""),
                    "line": None,
                    "title": item.get("title", ""),
                    "detail": item.get("detail", ""),
                    "evidence": "",
                }
            )

    return {
        "summary": sections["summary"] or "No summary provided.",
        "findings": findings,
        "analyze": sections["analyze"] or "none",
        "raw_markdown": text,
    }


def normalize_report(
    *,
    pr: int | None,
    label: str,
    base: str,
    head: str,
    model: str,
    changed_files: list[str],
    analyze_text: str,
    review_text: str,
) -> dict[str, Any]:
    parsed = extract_json_payload(review_text)
    if parsed is None:
        parsed = parse_markdown_review(review_text)
    else:
        parsed.setdefault("findings", [])
        parsed.setdefault("summary", "")
        parsed.setdefault("analyze", analyze_text)
        parsed["raw_markdown"] = review_text

    findings = []
    for item in parsed.get("findings") or []:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "nit")).lower()
        if severity in {"blockers", "blocker", "critical", "error"}:
            severity = "blocker"
        elif severity in {"should_fix", "should-fix", "warning", "major"}:
            severity = "should_fix"
        else:
            severity = "nit"
        findings.append(
            {
                "severity": severity,
                "file": item.get("file") or "",
                "line": item.get("line"),
                "title": item.get("title") or item.get("detail") or "Finding",
                "detail": item.get("detail") or "",
                "evidence": item.get("evidence") or "",
            }
        )

    blockers = sum(1 for f in findings if f["severity"] == "blocker")
    should_fix = sum(1 for f in findings if f["severity"] == "should_fix")
    nits = sum(1 for f in findings if f["severity"] == "nit")

    return {
        "pr": pr,
        "label": label,
        "base": base,
        "head": head,
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "changed_files": changed_files,
        "summary": parsed.get("summary") or "No summary provided.",
        "analyze": parsed.get("analyze") or analyze_text or "none",
        "findings": findings,
        "counts": {
            "blockers": blockers,
            "should_fix": should_fix,
            "nits": nits,
            "total": len(findings),
        },
        "raw_markdown": parsed.get("raw_markdown") or review_text,
    }


def _finding_rows(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "<tr><td colspan='4'>No findings.</td></tr>"
    rows = []
    for f in findings:
        sev = _esc(f["severity"])
        loc = _esc(f["file"] or "—")
        if f.get("line"):
            loc = f"{loc}:{_esc(f['line'])}"
        rows.append(
            f"<tr class='sev-{sev}'>"
            f"<td><span class='pill {sev}'>{sev}</span></td>"
            f"<td><code>{loc}</code></td>"
            f"<td><strong>{_esc(f['title'])}</strong>"
            f"<div class='muted'>{_esc(f['detail'])}</div></td>"
            f"<td class='muted'>{_esc(f.get('evidence') or '—')}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _findings_board(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "<div class='card muted'>No findings.</div>"
    cards = []
    for f in findings:
        sev = _esc(f["severity"])
        loc = _esc(f["file"] or "—")
        if f.get("line"):
            loc = f"{loc}:{_esc(f['line'])}"
        cards.append(
            "<article class='finding-card'>"
            f"<div class='finding-head'>"
            f"<strong>{_esc(f['title'])}</strong>"
            f"<span class='pill {sev}'>{sev}</span>"
            f"</div>"
            f"<p class='muted claim'>{_esc(f['detail'] or '—')}</p>"
            f"<div class='finding-meta'><code>{loc}</code></div>"
            f"<p><span class='label'>Evidence</span> "
            f"{_esc(f.get('evidence') or '—')}</p>"
            "</article>"
        )
    return "<div class='board'>" + "\n".join(cards) + "</div>"


def _recommended_layout_section() -> str:
    return """
    <h2>Recommended visual layout for future reviews</h2>
    <p class="muted">Prefer a structured report over free-form prose + full rewritten widgets.</p>
    <table>
      <thead>
        <tr><th>Section</th><th>What to show</th><th>Why</th></tr>
      </thead>
      <tbody>
        <tr><td>Header</td><td>PR number, branch, model, analyze status</td><td>Instant context</td></tr>
        <tr><td>Score strip</td><td>Blockers / Should fix / Nits</td><td>Scan severity first</td></tr>
        <tr><td>Findings board</td><td>file · line · claim · evidence</td><td>Actionable and scannable</td></tr>
        <tr><td>Analyze notes</td><td>flutter analyze items only</td><td>Ground truth vs speculation</td></tr>
        <tr><td>Skip</td><td>Full rewritten widget dumps</td><td>Hard to review; often invents APIs</td></tr>
      </tbody>
    </table>
"""


def render_pr_html(report: dict[str, Any]) -> str:
    counts = report["counts"]
    files = "".join(f"<li><code>{_esc(p)}</code></li>" for p in report["changed_files"])
    pr_label = f"PR #{report['pr']}" if report.get("pr") is not None else "Local branch"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(pr_label)} review</title>
  <style>
    :root {{
      --bg: #f7f5f1;
      --card: #fff;
      --ink: #1c1917;
      --muted: #78716c;
      --border: #e7e5e4;
      --accent: #0f766e;
      --danger: #b91c1c;
      --warn: #b45309;
      --ok: #047857;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.45;
    }}
    main {{ max-width: 980px; margin: 0 auto; padding: 28px 20px 48px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
    .stat, .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
    }}
    .stat .value {{ font-size: 28px; font-weight: 700; }}
    .stat .label {{ color: var(--muted); font-size: 13px; }}
    .value.danger {{ color: var(--danger); }}
    .value.warn {{ color: var(--warn); }}
    .value.ok {{ color: var(--ok); }}
    table {{ width: 100%; border-collapse: collapse; background: var(--card);
      border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 12px; vertical-align: top;
      border-bottom: 1px solid var(--border); font-size: 14px; }}
    th {{ background: #fafaf9; color: var(--muted); font-weight: 600; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px;
      font-size: 12px; font-weight: 600; background: #f5f5f4; }}
    .pill.blocker {{ background: #fee2e2; color: var(--danger); }}
    .pill.should_fix {{ background: #ffedd5; color: var(--warn); }}
    .pill.nit {{ background: #e7e5e4; color: #44403c; }}
    .board {{ display: grid; gap: 12px; }}
    .finding-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
    }}
    .finding-head {{
      display: flex; justify-content: space-between; gap: 12px;
      align-items: center; margin-bottom: 8px;
    }}
    .finding-meta {{ margin: 8px 0; }}
    .claim {{ margin: 0 0 4px; }}
    .label {{ font-weight: 600; color: var(--muted); font-size: 12px;
      text-transform: uppercase; letter-spacing: 0.02em; margin-right: 6px; }}
    ul {{ padding-left: 18px; }}
    pre {{
      white-space: pre-wrap;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      font-size: 12px;
    }}
    a {{ color: var(--accent); }}
    @media (max-width: 720px) {{
      .grid {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <p class="muted"><a href="../index.html">All PR reviews</a></p>
    <h1>{_esc(pr_label)} review</h1>
    <p class="muted">{_esc(report.get("label") or "")}</p>
    <p class="muted">
      {_esc(report["base"])}...{_esc(report["head"])}
      · model {_esc(report["model"])}
      · {_esc(report["generated_at"])}
    </p>

    <div class="grid" style="margin-top: 18px;">
      <div class="stat"><div class="value">{counts["total"]}</div><div class="label">Total findings</div></div>
      <div class="stat"><div class="value danger">{counts["blockers"]}</div><div class="label">Blockers</div></div>
      <div class="stat"><div class="value warn">{counts["should_fix"]}</div><div class="label">Should fix</div></div>
      <div class="stat"><div class="value ok">{counts["nits"]}</div><div class="label">Nits</div></div>
    </div>

    <h2>Summary</h2>
    <div class="card">{_esc(report["summary"])}</div>

    <h2>Findings board</h2>
    <p class="muted">Each card is one claim from the model, with severity and evidence.</p>
    {_findings_board(report["findings"])}

    <h2>Findings table</h2>
    <table>
      <thead>
        <tr><th>Severity</th><th>Location</th><th>Finding</th><th>Evidence</th></tr>
      </thead>
      <tbody>
        {_finding_rows(report["findings"])}
      </tbody>
    </table>

    {_recommended_layout_section()}

    <h2>Changed files</h2>
    <div class="card"><ul>{files or "<li>None</li>"}</ul></div>

    <h2>Analyze notes</h2>
    <pre>{_esc(report.get("analyze") or "none")}</pre>

    <h2>Raw model output</h2>
    <pre>{_esc(report.get("raw_markdown") or "")}</pre>
  </main>
</body>
</html>
"""


def render_index_html(reports: list[dict[str, Any]]) -> str:
    rows = []
    for report in sorted(
        reports,
        key=lambda r: (r.get("pr") is None, -(r.get("pr") or 0), r.get("generated_at", "")),
        reverse=False,
    ):
        pr = report.get("pr")
        href = f"pr-{pr}/index.html" if pr is not None else "local/index.html"
        label = f"PR #{pr}" if pr is not None else "Local branch"
        counts = report.get("counts") or {}
        rows.append(
            "<tr>"
            f"<td><a href='{_esc(href)}'>{_esc(label)}</a></td>"
            f"<td>{_esc(report.get('label') or '')}</td>"
            f"<td>{counts.get('blockers', 0)}</td>"
            f"<td>{counts.get('should_fix', 0)}</td>"
            f"<td>{counts.get('nits', 0)}</td>"
            f"<td class='muted'>{_esc(report.get('generated_at') or '')}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='6'>No reviews yet.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PR review reports</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #f7f5f1; color: #1c1917; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 28px 20px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e7e5e4; border-radius: 12px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid #e7e5e4; }}
    th {{ color: #78716c; }}
    a {{ color: #0f766e; }}
    .muted {{ color: #78716c; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <h1>PR review reports</h1>
    <p class="muted">Like coverage HTML: regenerated each time you run the reviewer. Per-PR folders update until you stop reviewing that PR.</p>
    <table>
      <thead>
        <tr><th>PR</th><th>Label</th><th>Blockers</th><th>Should fix</th><th>Nits</th><th>Updated</th></tr>
      </thead>
      <tbody>
        {body}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def write_report(report: dict[str, Any], *, open_browser: bool = True) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pr = report.get("pr")
    folder = REPORTS_DIR / (f"pr-{pr}" if pr is not None else "local")
    folder.mkdir(parents=True, exist_ok=True)

    json_path = folder / "report.json"
    html_path = folder / "index.html"
    md_path = folder / "review.md"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    html_path.write_text(render_pr_html(report), encoding="utf-8")
    md_path.write_text(str(report.get("raw_markdown") or ""), encoding="utf-8")

    # Refresh dashboard of all reports
    reports: list[dict[str, Any]] = []
    for child in REPORTS_DIR.iterdir():
        candidate = child / "report.json"
        if candidate.is_file():
            try:
                reports.append(json.loads(candidate.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    (REPORTS_DIR / "index.html").write_text(
        render_index_html(reports),
        encoding="utf-8",
    )

    # latest pointer copy for quick open
    latest = REPORTS_DIR / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "index.html").write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
    (latest / "report.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    if open_browser:
        import subprocess
        import sys

        if sys.platform == "darwin":
            subprocess.run(["open", str(html_path)], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(html_path)], check=False)

    return html_path
