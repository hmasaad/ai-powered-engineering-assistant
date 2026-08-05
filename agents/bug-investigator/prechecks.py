#!/usr/bin/env python3
"""Deterministic bug-pattern prechecks for the Bug Investigation Agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


FileLoader = Callable[[str], str | None]

STACK_PATH_RE = re.compile(
    r"(?:package:salon_booking/|(?:^|\s)(?:lib/))([\w/.-]+\.dart)(?:[:\s]+(\d+))?",
    re.MULTILINE,
)


@dataclass
class PrecheckFinding:
    file: str
    line: int
    severity: str
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


def _line_at(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def parse_stack_paths(stacktrace: str) -> list[tuple[str, int]]:
    """Return (lib-relative path, line) pairs from a Flutter/Dart stacktrace."""
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    text = stacktrace or ""
    for match in STACK_PATH_RE.finditer(text):
        rel = match.group(1).replace("\\", "/")
        if not rel.startswith("lib/"):
            rel = f"lib/{rel}"
        line = int(match.group(2) or "1")
        key = f"{rel}:{line}"
        if key in seen:
            continue
        seen.add(key)
        found.append((rel, line))
    return found


def parse_analyze_for_paths(
    analyze_text: str,
    reviewed_paths: list[str],
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    path_set = set(reviewed_paths)
    pattern = re.compile(
        r"^\s*(error|warning|info)\s+[•·-]\s+(.+?)\s+[•·-]\s+"
        r"([^:]+):(\d+):\d+",
        re.IGNORECASE | re.MULTILINE,
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
        level_l = level.lower()
        if level_l == "info":
            continue
        severity = "blocker" if level_l == "error" else "should_fix"
        findings.append(
            PrecheckFinding(
                file=path,
                line=int(line_s),
                severity=severity,
                explanation=f"flutter analyze {level_l}: {message.strip()}",
                recommendation="Fix the analyzer issue; it may be the crash/compile root cause.",
                confidence=0.95 if level_l == "error" else 0.85,
                evidence=f"analyze:{path}:{line_s}",
                check_id="flutter_analyze",
            )
        )
    return findings


def check_empty_catch(paths: list[str], loader: FileLoader) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    pattern = re.compile(
        r"catch\s*\([^)]*\)\s*\{\s*\}|on\s+\w+\s*\{\s*\}",
        re.MULTILINE,
    )
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        for match in pattern.finditer(content):
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="should_fix",
                    explanation="Empty catch swallows failures and hides the real bug.",
                    recommendation="Log/emit the error into ScreenState or rethrow after handling.",
                    confidence=0.9,
                    evidence=f"precheck:empty_catch:{path}",
                    check_id="empty_catch",
                )
            )
    return findings


def check_ignored_response_exception(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    """ResponseEntity used without checking `.exception` nearby."""
    findings: list[PrecheckFinding] = []
    for path in paths:
        if "/blocs/" not in path and "/ui/" not in path:
            continue
        content = loader(path)
        if not content:
            continue
        for match in re.finditer(
            r"(?:final|var|await)?\s*\w*\s*=?\s*await\s+[^\n;]+;",
            content,
        ):
            window_start = match.start()
            window = content[window_start : window_start + 500]
            if "ResponseEntity" not in window and not re.search(
                r"await\s+_\w*(service|api|Service|Api)\.", window
            ):
                # still check common service awaits
                if not re.search(r"await\s+_\w+\.", window):
                    continue
            if ".exception" in window or "hasException" in window:
                continue
            if "if (" in window and "exception" in window.lower():
                continue
            # require response-like variable usage
            if not re.search(r"\bresponse\b|\.data\b", window, re.I):
                continue
            if ".data" in window and ".exception" not in window:
                findings.append(
                    PrecheckFinding(
                        file=path,
                        line=_line_at(content, window_start),
                        severity="should_fix",
                        explanation=(
                            "Service/API response `.data` is used without checking "
                            "`.exception` — failures may look like empty success."
                        ),
                        recommendation=(
                            "Branch on response.exception before using response.data "
                            "and emit ScreenState.error / submitError."
                        ),
                        confidence=0.78,
                        evidence=f"precheck:ignored_response_exception:{path}",
                        check_id="ignored_response_exception",
                    )
                )
    return findings


def check_bang_operators(paths: list[str], loader: FileLoader) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    for path in paths:
        if "/ui/" not in path or not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            code = line.split("//", 1)[0]
            if "!" not in code:
                continue
            for match in re.finditer(r"\b([A-Za-z_]\w*(?:\.\w+)*)!(?![=\w])", code):
                name = match.group(1)
                # Skip if a nearby preceding line already null-checked this value.
                window = "\n".join(lines[max(0, i - 6) : i])
                if re.search(
                    rf"{re.escape(name)}\s*!=\s*null|{re.escape(name)}\s*==\s*null",
                    window,
                ):
                    continue
                findings.append(
                    PrecheckFinding(
                        file=path,
                        line=i,
                        severity="should_fix",
                        explanation=(
                            f"Null assertion `{match.group(0)}` can crash if the value "
                            "is null on this UI path."
                        ),
                        recommendation=(
                            "Replace with a null check / early return / ?./?? before use."
                        ),
                        confidence=0.72,
                        evidence=f"precheck:null_bang:{path}:{i}",
                        check_id="null_bang",
                    )
                )
                break
    return findings


def check_setstate_without_mounted(
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
        if "setState" not in content:
            continue
        # async callback then setState without mounted
        for match in re.finditer(
            r"(async\s*\{[\s\S]{0,400}?setState\s*\()",
            content,
        ):
            block = match.group(1)
            if "mounted" in block:
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="should_fix",
                    explanation=(
                        "setState after an await without a mounted check can throw "
                        "after the widget is disposed."
                    ),
                    recommendation="Guard with `if (!mounted) return;` before setState.",
                    confidence=0.8,
                    evidence=f"precheck:setstate_unmounted:{path}",
                    check_id="setstate_unmounted",
                )
            )
    return findings


def check_fire_and_forget_async(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    """Sync handlers calling un-awaited private async loaders (common BLoC bug)."""
    findings: list[PrecheckFinding] = []
    for path in paths:
        if "/blocs/" not in path:
            continue
        content = loader(path)
        if not content:
            continue
        # Find Future<void> _foo methods
        async_methods = {
            m.group(1)
            for m in re.finditer(
                r"Future(?:<[^>]+>)?\s+(_\w+)\s*\(",
                content,
            )
        }
        if not async_methods:
            continue
        for name in sorted(async_methods):
            # calls like _loadSlots(...); not awaited
            for match in re.finditer(rf"(?<!await\s)\b{re.escape(name)}\s*\(", content):
                # skip the definition line
                line_start = content.rfind("\n", 0, match.start()) + 1
                header = content[line_start : match.start()]
                if re.search(r"Future(?:<[^>]+>)?\s+$", header):
                    continue
                findings.append(
                    PrecheckFinding(
                        file=path,
                        line=_line_at(content, match.start()),
                        severity="should_fix",
                        explanation=(
                            f"Async `{name}` is invoked without await; errors can be "
                            "swallowed and loading state may never settle."
                        ),
                        recommendation=(
                            "Await the Future in the event handler (or use "
                            "unawaited with explicit error handling)."
                        ),
                        confidence=0.76,
                        evidence=f"precheck:fire_and_forget_async:{path}:{name}",
                        check_id="fire_and_forget_async",
                    )
                )
    return findings


def check_ui_missing_error_branch(
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
        if "ScreenState" not in content and "BlocBuilder" not in content:
            continue
        if "ScreenState.error" in content or "FullScreenError" in content:
            continue
        if "BlocBuilder" not in content:
            continue
        findings.append(
            PrecheckFinding(
                file=path,
                line=_line_at(content, content.find("BlocBuilder")),
                severity="nit",
                explanation=(
                    "UI builds from Bloc state but has no ScreenState.error / "
                    "FullScreenError branch — failures may render as blank/stuck UI."
                ),
                recommendation="Handle ScreenState.error with FullScreenError or inline error UI.",
                confidence=0.7,
                evidence=f"precheck:ui_missing_error_branch:{path}",
                check_id="ui_missing_error_branch",
            )
        )
    return findings


def check_stack_targets(
    stacktrace: str,
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    for path, line in parse_stack_paths(stacktrace):
        content = loader(path)
        if content is None:
            continue
        findings.append(
            PrecheckFinding(
                file=path,
                line=line,
                severity="should_fix",
                explanation=(
                    "Stacktrace points here — treat as a primary investigation target."
                ),
                recommendation=(
                    "Inspect this frame and its callers in the UI→Bloc→Service chain."
                ),
                confidence=0.88,
                evidence=f"stack:{path}:{line}",
                check_id="stack_frame",
            )
        )
    return findings


def default_scope_paths(root: Path) -> list[str]:
    paths: list[str] = []
    lib = root / "lib"
    if not lib.is_dir():
        return paths
    for path in sorted(lib.rglob("*.dart")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        if any(part.startswith(".") for part in path.parts):
            continue
        paths.append(rel)
    return paths[:80]


def run_prechecks(
    *,
    reviewed_paths: list[str],
    diff_text: str,  # noqa: ARG001
    analyze_text: str,
    loader: FileLoader,
    root: Path,  # noqa: ARG001
    stacktrace: str = "",
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    findings.extend(parse_analyze_for_paths(analyze_text, reviewed_paths))
    findings.extend(check_stack_targets(stacktrace, loader))
    findings.extend(check_empty_catch(reviewed_paths, loader))
    findings.extend(check_ignored_response_exception(reviewed_paths, loader))
    findings.extend(check_bang_operators(reviewed_paths, loader))
    findings.extend(check_setstate_without_mounted(reviewed_paths, loader))
    findings.extend(check_fire_and_forget_async(reviewed_paths, loader))
    findings.extend(check_ui_missing_error_branch(reviewed_paths, loader))

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
        return "(no deterministic bug precheck findings)"
    lines = [
        "These issues were already detected deterministically. "
        "Do NOT duplicate them as new findings; use them as investigation leads."
    ]
    for item in findings:
        lines.append(
            f"- [{item.severity}] `{item.file}:{item.line}` "
            f"({item.evidence}) {item.explanation}"
        )
    return "\n".join(lines)


def precheck_evidence_ids(findings: list[PrecheckFinding]) -> set[str]:
    return {f.evidence for f in findings} | {f"precheck:{f.check_id}" for f in findings}
