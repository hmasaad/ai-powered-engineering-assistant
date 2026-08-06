#!/usr/bin/env python3
"""Deterministic Flutter performance prechecks (before the LLM).

Findings are high-confidence and evidence-bound (`precheck:<id>`).
The model should focus on residual performance risk not already covered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


FileLoader = Callable[[str], str | None]


@dataclass
class PrecheckFinding:
    file: str
    line: int
    severity: str  # blocker | should_fix | nit
    explanation: str
    recommendation: str
    confidence: float
    evidence: str
    check_id: str
    source: str = "precheck"

    def as_finding(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "explanation": self.explanation,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "source": "precheck",
            "check_id": self.check_id,
        }


def _line_of(content: str, needle: str) -> int:
    idx = content.find(needle)
    if idx < 0:
        return 1
    return content.count("\n", 0, idx) + 1


def _line_at(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def parse_analyze_for_paths(
    analyze_text: str,
    reviewed_paths: list[str],
) -> list[PrecheckFinding]:
    """Surface analyzer hits on changed files that often correlate with perf."""
    findings: list[PrecheckFinding] = []
    path_set = set(reviewed_paths)
    pattern = re.compile(
        r"^\s*(error|warning|info)\s+[•·-]\s+(.+?)\s+[•·-]\s+"
        r"([^:]+):(\d+):\d+",
        re.IGNORECASE | re.MULTILINE,
    )
    perf_keywords = (
        "avoid",
        "performance",
        "rebuild",
        "const",
        "unnecessary",
        "dispose",
        "leak",
        "async",
        "unawaited",
    )
    for match in pattern.finditer(analyze_text or ""):
        level, message, path, line_s = match.groups()
        path = path.strip().replace("\\", "/")
        if path not in path_set:
            for candidate in path_set:
                if path.endswith(candidate) or candidate.endswith(path):
                    path = candidate
                    break
            else:
                continue
        msg = message.strip()
        if not any(k in msg.lower() for k in perf_keywords) and level.lower() != "error":
            continue
        level_l = level.lower()
        severity = (
            "blocker"
            if level_l == "error"
            else "should_fix"
            if level_l == "warning"
            else "nit"
        )
        findings.append(
            PrecheckFinding(
                file=path,
                line=int(line_s),
                severity=severity,
                explanation=f"flutter analyze {level_l}: {msg}",
                recommendation="Fix the analyzer issue; it often maps to rebuild/leak waste.",
                confidence=0.9 if level_l == "error" else 0.8,
                evidence=f"analyze:{path}:{line_s}",
                check_id="flutter_analyze_perf",
            )
        )
    return findings


def check_image_network_uncached(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    for path in paths:
        if "/ui/" not in path or not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        for match in re.finditer(r"Image\.network\s*\(", content):
            start = match.start()
            # approximate call body until matching paren depth or 400 chars
            snippet = content[start : start + 500]
            if re.search(r"cacheWidth\s*:", snippet) or re.search(
                r"cacheHeight\s*:", snippet
            ):
                continue
            if "CachedNetworkImage" in content[max(0, start - 80) : start]:
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, start),
                    severity="should_fix",
                    explanation=(
                        "Image.network without cacheWidth/cacheHeight can decode "
                        "full-resolution bitmaps during scroll and cause jank."
                    ),
                    recommendation=(
                        "Pass cacheWidth/cacheHeight for the display size, or use a "
                        "caching image widget/provider."
                    ),
                    confidence=0.82,
                    evidence=f"precheck:image_network_uncached:{path}",
                    check_id="image_network_uncached",
                )
            )
    return findings


def check_eager_listview(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    """Flag ListView/GridView(children: ...) for dynamic/eager child lists."""
    findings: list[PrecheckFinding] = []
    ctor = re.compile(
        r"\b(ListView|GridView|PageView)\s*\(\s*(?!builder|separated)",
        re.MULTILINE,
    )
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        for match in ctor.finditer(content):
            start = match.start()
            window = content[start : start + 600]
            # skip if it's clearly ListView.builder / .separated (false via look)
            if re.match(r"(ListView|GridView|PageView)\.(builder|separated)\b", content[start:]):
                continue
            if "children:" not in window:
                continue
            # Only flag dynamic / generated children — not small static forms.
            dynamic = bool(
                re.search(
                    r"children:\s*\w+\.map|"
                    r"children:\s*\w+\.toList|"
                    r"children:\s*\[\s*\.\.\.|"
                    r"children:\s*\.\.\.|"
                    r"\.map\s*\(",
                    window,
                )
            )
            if not dynamic:
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, start),
                    severity="should_fix",
                    explanation=(
                        f"{match.group(1)}(children: …) builds all children up front; "
                        "long or dynamic lists should use a builder constructor."
                    ),
                    recommendation=(
                        f"Prefer {match.group(1)}.builder or {match.group(1)}.separated "
                        "so off-screen items are not built eagerly."
                    ),
                    confidence=0.8,
                    evidence=f"precheck:eager_listview:{path}",
                    check_id="eager_listview",
                )
            )
    return findings


def check_shrink_wrap(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    for path in paths:
        if not path.endswith(".dart") or "/ui/" not in path:
            continue
        content = loader(path)
        if not content:
            continue
        for match in re.finditer(r"shrinkWrap\s*:\s*true", content):
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="nit",
                    explanation=(
                        "shrinkWrap: true forces the scrollable to measure all children, "
                        "which is costly for long lists."
                    ),
                    recommendation=(
                        "Avoid shrinkWrap when possible; use Expanded/Flexible or a "
                        "CustomScrollView with slivers instead of nested measuring."
                    ),
                    confidence=0.78,
                    evidence=f"precheck:shrink_wrap:{path}",
                    check_id="shrink_wrap",
                )
            )
    return findings


def check_bloc_builder_without_build_when(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    for path in paths:
        if not path.endswith(".dart") or "/ui/" not in path:
            continue
        content = loader(path)
        if not content:
            continue
        for match in re.finditer(r"\bBlocBuilder\s*<", content):
            start = match.start()
            # find approximate closing of constructor args (up to 800 chars)
            window = content[start : start + 900]
            if "buildWhen" in window:
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, start),
                    severity="nit",
                    explanation=(
                        "BlocBuilder has no buildWhen; every state emission rebuilds "
                        "this subtree, which can waste frames on busy screens."
                    ),
                    recommendation=(
                        "Add buildWhen to rebuild only when the fields this widget "
                        "reads actually change."
                    ),
                    confidence=0.7,
                    evidence=f"precheck:bloc_builder_no_build_when:{path}",
                    check_id="bloc_builder_no_build_when",
                )
            )
    return findings


def check_future_created_in_build(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    """FutureBuilder(future: someCall()) recreates futures every rebuild."""
    findings: list[PrecheckFinding] = []
    pattern = re.compile(
        r"FutureBuilder\s*(?:<[^>]+>)?\s*\(\s*(?:[\s\S]*?)future\s*:\s*([^,\n]+)",
        re.MULTILINE,
    )
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        for match in pattern.finditer(content):
            expr = match.group(1).strip()
            # field/reference is ok; call or constructor is not
            if re.search(r"\w+\s*\(", expr) and not re.match(r"^[_a-zA-Z]\w*$", expr):
                findings.append(
                    PrecheckFinding(
                        file=path,
                        line=_line_at(content, match.start()),
                        severity="should_fix",
                        explanation=(
                            "FutureBuilder creates a new Future in build, so the future "
                            "restarts on every rebuild."
                        ),
                        recommendation=(
                            "Create the Future once (initState / bloc) and pass a stable "
                            "reference into FutureBuilder."
                        ),
                        confidence=0.86,
                        evidence=f"precheck:future_in_build:{path}",
                        check_id="future_in_build",
                    )
                )
    return findings


def check_sync_heavy_work(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    heavy = re.compile(
        r"\b(jsonDecode|json\.decode|readAsStringSync|readAsBytesSync|"
        r"File\([^)]*\)\.readAsStringSync|gzip\.decode)\s*\(",
    )
    for path in paths:
        if not path.endswith(".dart"):
            continue
        # Allow sample APIs that intentionally simulate work; still flag UI/bloc
        if "/ui/" not in path and "/blocs/" not in path:
            continue
        content = loader(path)
        if not content:
            continue
        for match in heavy.finditer(content):
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="blocker" if "/ui/" in path else "should_fix",
                    explanation=(
                        f"Synchronous heavy work (`{match.group(1)}`) on the UI/"
                        "bloc path can stall frames."
                    ),
                    recommendation=(
                        "Move parsing/I/O off the UI isolate with compute()/Isolate, "
                        "or keep it in the API/service layer with async APIs."
                    ),
                    confidence=0.88,
                    evidence=f"precheck:sync_heavy_work:{path}",
                    check_id="sync_heavy_work",
                )
            )
    return findings


def check_opacity_for_animation(
    paths: list[str],
    diff_text: str,
) -> list[PrecheckFinding]:
    """Opacity in added lines is a common animation anti-pattern."""
    findings: list[PrecheckFinding] = []
    current: str | None = None
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].replace("\\", "/")
            continue
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            new_line = int(match.group(1)) - 1
            continue
        if current is None or current not in paths:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            new_line += 1
            body = line[1:]
            if re.search(r"\bOpacity\s*\(", body):
                findings.append(
                    PrecheckFinding(
                        file=current,
                        line=new_line,
                        severity="nit",
                        explanation=(
                            "Opacity saves an offscreen layer and is costly when "
                            "animated; prefer FadeTransition/AnimatedOpacity patterns."
                        ),
                        recommendation=(
                            "For fades, use AnimatedOpacity or FadeTransition instead "
                            "of animating Opacity directly when possible."
                        ),
                        confidence=0.72,
                        evidence=f"diff_hunk:{current}:{new_line}",
                        check_id="opacity_widget",
                    )
                )
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("\\"):
            continue
        else:
            new_line += 1
    return findings


def check_nested_listviews(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    for path in paths:
        if not path.endswith(".dart") or "/ui/" not in path:
            continue
        content = loader(path)
        if not content:
            continue
        # crude: ListView containing another ListView/GridView in same file region
        for outer in re.finditer(r"\bListView(?:\.(?:builder|separated))?\s*\(", content):
            start = outer.start()
            region = content[start : start + 2500]
            inner = re.search(
                r"\b(ListView|GridView)(?:\.(?:builder|separated))?\s*\(",
                region[len(outer.group(0)) :],
            )
            if not inner:
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, start + len(outer.group(0)) + inner.start()),
                    severity="should_fix",
                    explanation=(
                        "Nested scrollables (ListView inside ListView) often cause "
                        "gesture conflicts and expensive layout."
                    ),
                    recommendation=(
                        "Use a single CustomScrollView with slivers, or give the inner "
                        "list a fixed height with a non-nested builder carefully."
                    ),
                    confidence=0.75,
                    evidence=f"precheck:nested_listview:{path}",
                    check_id="nested_listview",
                )
            )
            break  # one finding per file is enough signal
    return findings


def run_prechecks(
    *,
    reviewed_paths: list[str],
    diff_text: str,
    analyze_text: str,
    loader: FileLoader,
    root: Path,  # noqa: ARG001 — kept for API parity with PR reviewer
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    findings.extend(parse_analyze_for_paths(analyze_text, reviewed_paths))
    findings.extend(check_image_network_uncached(reviewed_paths, loader))
    findings.extend(check_eager_listview(reviewed_paths, loader))
    findings.extend(check_shrink_wrap(reviewed_paths, loader))
    findings.extend(check_bloc_builder_without_build_when(reviewed_paths, loader))
    findings.extend(check_future_created_in_build(reviewed_paths, loader))
    findings.extend(check_sync_heavy_work(reviewed_paths, loader))
    findings.extend(check_opacity_for_animation(reviewed_paths, diff_text))
    findings.extend(check_nested_listviews(reviewed_paths, loader))

    seen: set[str] = set()
    unique: list[PrecheckFinding] = []
    for item in findings:
        key = f"{item.file}:{item.line}:{item.check_id}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def format_prechecks_for_prompt(findings: list[PrecheckFinding]) -> str:
    if not findings:
        return "(no deterministic performance precheck findings)"
    lines = [
        "These performance issues were already detected deterministically. "
        "Do NOT duplicate them; focus on residual risks."
    ]
    for item in findings:
        lines.append(
            f"- [{item.severity}] `{item.file}:{item.line}` "
            f"({item.evidence}) {item.explanation}"
        )
    return "\n".join(lines)


def precheck_evidence_ids(findings: list[PrecheckFinding]) -> set[str]:
    return {f.evidence for f in findings} | {f"precheck:{f.check_id}" for f in findings}
