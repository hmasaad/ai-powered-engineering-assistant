#!/usr/bin/env python3
"""Deterministic prechecks that run before the LLM.

Findings here are high-confidence and evidence-bound (`precheck:<id>`).
The model should focus on residual risk not already covered.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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
        data = asdict(self)
        data.pop("check_id", None)
        data.pop("source", None)
        # keep source for reports
        out = {
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
        return out


def _line_of(content: str, needle: str) -> int:
    idx = content.find(needle)
    if idx < 0:
        return 1
    return content.count("\n", 0, idx) + 1


def parse_analyze_for_paths(
    analyze_text: str,
    reviewed_paths: list[str],
) -> list[PrecheckFinding]:
    """Turn flutter analyze lines that mention changed files into findings."""
    findings: list[PrecheckFinding] = []
    path_set = set(reviewed_paths)
    # e.g.  error • message • lib/foo.dart:12:3 • code
    pattern = re.compile(
        r"^\s*(error|warning|info)\s+[•·-]\s+(.+?)\s+[•·-]\s+"
        r"([^:]+):(\d+):\d+",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in pattern.finditer(analyze_text or ""):
        level, message, path, line_s = match.groups()
        path = path.strip().replace("\\", "/")
        if path not in path_set:
            # sometimes analyze prints absolute or relative with prefix
            for candidate in path_set:
                if path.endswith(candidate) or candidate.endswith(path):
                    path = candidate
                    break
            else:
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
                explanation=f"flutter analyze {level_l}: {message.strip()}",
                recommendation="Fix the analyzer issue or justify a suppression.",
                confidence=0.95 if level_l == "error" else 0.85,
                evidence=f"analyze:{path}:{line_s}",
                check_id="flutter_analyze",
            )
        )
    return findings


def check_layer_violations(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        imports = re.findall(r"""^import\s+['"]([^'"]+)['"]""", content, re.M)

        # UI must not import api/ directly
        if "/ui/" in path:
            for imp in imports:
                if "/api/" in imp or imp.startswith("package:salon_booking/api/"):
                    findings.append(
                        PrecheckFinding(
                            file=path,
                            line=_line_of(content, imp),
                            severity="blocker",
                            explanation=(
                                "UI layer imports API directly; "
                                "screens should go UI → Bloc → Service → Api."
                            ),
                            recommendation=(
                                "Remove the API import and call the bloc/service instead."
                            ),
                            confidence=0.92,
                            evidence=f"precheck:ui_imports_api:{path}",
                            check_id="ui_imports_api",
                        )
                    )

        # Bloc must not import api/ (should use service)
        if "/blocs/" in path:
            for imp in imports:
                if "/api/" in imp or imp.startswith("package:salon_booking/api/"):
                    findings.append(
                        PrecheckFinding(
                            file=path,
                            line=_line_of(content, imp),
                            severity="should_fix",
                            explanation=(
                                "Bloc imports API directly; prefer Service wrappers."
                            ),
                            recommendation=(
                                "Inject/use a Service and keep Api behind services/."
                            ),
                            confidence=0.88,
                            evidence=f"precheck:bloc_imports_api:{path}",
                            check_id="bloc_imports_api",
                        )
                    )

        # Service must not import ui/
        if "/services/" in path:
            for imp in imports:
                if "/ui/" in imp or imp.startswith("package:salon_booking/ui/"):
                    findings.append(
                        PrecheckFinding(
                            file=path,
                            line=_line_of(content, imp),
                            severity="blocker",
                            explanation="Service imports UI — layering is inverted.",
                            recommendation="Remove UI imports from services.",
                            confidence=0.95,
                            evidence=f"precheck:service_imports_ui:{path}",
                            check_id="service_imports_ui",
                        )
                    )
    return findings


def check_di_registration(
    paths: list[str],
    loader: FileLoader,
    *,
    root: Path,
) -> list[PrecheckFinding]:
    """If a new Bloc/Service appears in changed files, require injector registration."""
    findings: list[PrecheckFinding] = []
    injector = root / "lib/inject/injector.dart"
    injector_text = ""
    if injector.is_file():
        injector_text = injector.read_text(encoding="utf-8", errors="replace")

    for path in paths:
        content = loader(path)
        if not content:
            continue
        if "/blocs/" in path:
            for match in re.finditer(r"class\s+(\w+Bloc)\b", content):
                name = match.group(1)
                if name == "BaseBloc":
                    continue
                if name not in injector_text:
                    findings.append(
                        PrecheckFinding(
                            file=path,
                            line=_line_of(content, match.group(0)),
                            severity="should_fix",
                            explanation=(
                                f"{name} is not referenced in lib/inject/injector.dart."
                            ),
                            recommendation=(
                                f"Register {name} with get_it in Injector.setup."
                            ),
                            confidence=0.8,
                            evidence=f"precheck:missing_di:{name}",
                            check_id="missing_di_registration",
                        )
                    )
        if "/services/" in path:
            for match in re.finditer(r"class\s+(\w+Service)\b", content):
                name = match.group(1)
                if name not in injector_text:
                    findings.append(
                        PrecheckFinding(
                            file=path,
                            line=_line_of(content, match.group(0)),
                            severity="should_fix",
                            explanation=(
                                f"{name} is not referenced in lib/inject/injector.dart."
                            ),
                            recommendation=(
                                f"Register {name} with get_it in Injector.setup."
                            ),
                            confidence=0.8,
                            evidence=f"precheck:missing_di:{name}",
                            check_id="missing_di_registration",
                        )
                    )
    return findings


def check_debug_leftovers(
    paths: list[str],
    loader: FileLoader,
    diff_text: str,
) -> list[PrecheckFinding]:
    """Flag print/debugPrint added in the diff."""
    findings: list[PrecheckFinding] = []
    # only look at added lines in reviewed files
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
            if re.search(r"\b(print|debugPrint)\s*\(", body):
                findings.append(
                    PrecheckFinding(
                        file=current,
                        line=new_line,
                        severity="nit",
                        explanation="Debug print added in this diff.",
                        recommendation="Remove print/debugPrint before merge.",
                        confidence=0.9,
                        evidence=f"diff_hunk:{current}:{new_line}",
                        check_id="debug_print",
                    )
                )
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("\\"):
            continue
        else:
            new_line += 1
    return findings


def check_todo_fixme(
    paths: list[str],
    diff_text: str,
) -> list[PrecheckFinding]:
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
            if re.search(r"\b(TODO|FIXME|HACK)\b", line[1:]):
                findings.append(
                    PrecheckFinding(
                        file=current,
                        line=new_line,
                        severity="nit",
                        explanation="TODO/FIXME/HACK introduced in this change.",
                        recommendation="Resolve or track the follow-up before merge.",
                        confidence=0.75,
                        evidence=f"diff_hunk:{current}:{new_line}",
                        check_id="todo_fixme",
                    )
                )
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("\\"):
            continue
        else:
            new_line += 1
    return findings


def run_prechecks(
    *,
    reviewed_paths: list[str],
    diff_text: str,
    analyze_text: str,
    loader: FileLoader,
    root: Path,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    findings.extend(parse_analyze_for_paths(analyze_text, reviewed_paths))
    findings.extend(check_layer_violations(reviewed_paths, loader))
    findings.extend(check_di_registration(reviewed_paths, loader, root=root))
    findings.extend(check_debug_leftovers(reviewed_paths, loader, diff_text))
    findings.extend(check_todo_fixme(reviewed_paths, diff_text))

    # de-dupe by file:line:check_id
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
        return "(no deterministic precheck findings)"
    lines = [
        "These issues were already detected deterministically. "
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
