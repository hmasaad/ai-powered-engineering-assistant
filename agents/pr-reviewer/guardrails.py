#!/usr/bin/env python3
"""v1 guardrails for the local PR reviewer.

Enforced before/after the model call:
1. Review only changed, reviewable files
2. Ignore generated / binary / dependency paths
3. Scan & redact secrets before prompting
4. Limit context to diff + nearby hunks
5. Require structured findings (file, line, severity, explanation, recommendation)
6. Drop low-confidence findings
7. Validate model output against a strict JSON schema
8. Never approve, merge, or modify PRs (read-only agent)
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Path policy
# ---------------------------------------------------------------------------

REVIEWABLE_SUFFIXES = {
    ".dart",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".gradle",
    ".kts",
    ".kt",
    ".swift",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".sh",
    ".py",
    ".toml",
}

IGNORE_PATH_GLOBS = (
    # dependencies / lockfiles
    "**/pubspec.lock",
    "**/Podfile.lock",
    "**/package-lock.json",
    "**/yarn.lock",
    "**/node_modules/**",
    "**/Pods/**",
    ".dart_tool/**",
    "**/.packages",
    # build / generated
    "**/build/**",
    "**/.gradle/**",
    "**/DerivedData/**",
    "**/*.g.dart",
    "**/*.freezed.dart",
    "**/*.mocks.dart",
    "**/*.gen.dart",
    "**/generated/**",
    "**/l10n/**/*.dart",
    "**/.flutter-plugins*",
    # binaries / media / archives
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.webp",
    "**/*.ico",
    "**/*.pdf",
    "**/*.zip",
    "**/*.jar",
    "**/*.aar",
    "**/*.so",
    "**/*.dylib",
    "**/*.dll",
    "**/*.exe",
    "**/*.bin",
    "**/*.sqlite",
    "**/*.db",
    # VCS / IDE
    "**/.git/**",
    "**/.idea/**",
    "**/.vscode/**",
    # RAG / review artifacts (do not re-review the index itself)
    "agents/pr-reviewer/index/**",
    "agents/pr-reviewer/reports/**",
)

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".jar",
    ".aar",
    ".apk",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".bin",
    ".sqlite",
    ".db",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
}

NEARBY_LINES = 25
MAX_HUNK_CHARS = 8_000
MAX_CONTEXT_CHARS = 40_000
DEFAULT_MIN_CONFIDENCE = 0.55
SEVERITIES = ("blocker", "should_fix", "nit")


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?"
            r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
            re.MULTILINE,
        ),
    ),
    (
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    (
        "stripe_key",
        re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
    ),
    (
        "generic_api_key_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
            r"client[_-]?secret|private[_-]?key|password|passwd)\b\s*[:=]\s*"
            r"['\"][^'\"]{8,}['\"]"
        ),
    ),
    (
        "connection_string",
        re.compile(
            r"(?i)\b(?:postgres|mysql|mongodb|redis)://[^\s'\"]+:[^\s'\"]+@[^\s'\"]+"
        ),
    ),
]

REDACTION = "[REDACTED_SECRET]"


@dataclass
class SecretHit:
    kind: str
    path: str
    excerpt: str


@dataclass
class GuardrailReport:
    skipped_paths: list[str] = field(default_factory=list)
    reviewed_paths: list[str] = field(default_factory=list)
    secrets: list[SecretHit] = field(default_factory=list)
    secrets_redacted: int = 0
    findings_dropped_low_confidence: int = 0
    findings_dropped_invalid: int = 0
    findings_dropped_no_evidence: int = 0
    findings_muted: int = 0
    schema_ok: bool = False
    blocked_send: bool = False
    notes: list[str] = field(default_factory=list)


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def is_ignored_path(path: str) -> bool:
    rel = normalize_path(path)
    if Path(rel).suffix.lower() in BINARY_SUFFIXES:
        return True
    name = Path(rel).name
    for pattern in IGNORE_PATH_GLOBS:
        variants = {pattern}
        if pattern.startswith("**/"):
            variants.add(pattern[3:])
        for candidate in variants:
            if fnmatch.fnmatch(rel, candidate):
                return True
            # directory prefix: "build/**" matches "build/app.apk"
            if candidate.endswith("/**"):
                prefix = candidate[:-3]
                if rel == prefix or rel.startswith(prefix + "/"):
                    return True
        if pattern.startswith("**/") and fnmatch.fnmatch(name, pattern[3:]):
            return True
        if "/" not in pattern and fnmatch.fnmatch(name, pattern):
            return True
    return False


def is_reviewable_path(path: str) -> bool:
    rel = normalize_path(path)
    if not rel or is_ignored_path(rel):
        return False
    suffix = Path(rel).suffix.lower()
    if suffix and suffix not in REVIEWABLE_SUFFIXES:
        return False
    return True


def filter_changed_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    """Return (reviewable, skipped) from changed-file list."""
    reviewed: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = normalize_path(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        if is_reviewable_path(path):
            reviewed.append(path)
        else:
            skipped.append(path)
    return reviewed, skipped


# ---------------------------------------------------------------------------
# Diff / nearby context
# ---------------------------------------------------------------------------

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_changed_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Map path -> list of (start, end) line ranges from unified diff + hunks."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = normalize_path(line[6:])
            ranges.setdefault(current, [])
            continue
        if line.startswith("diff --git "):
            current = None
            continue
        if current is None:
            continue
        match = HUNK_HEADER_RE.match(line)
        if not match:
            continue
        start = int(match.group(2))
        # approximate end from following +/context lines counted later
        ranges[current].append((start, start))
    return ranges


def refine_ranges_from_hunk_body(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Compute accurate new-file line ranges per hunk."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    new_line = 0
    hunk_start = 0
    in_hunk = False

    def close_hunk() -> None:
        nonlocal in_hunk
        if current and in_hunk and new_line >= hunk_start:
            ranges.setdefault(current, []).append((hunk_start, new_line))
        in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            close_hunk()
            current = None
            continue
        if line.startswith("+++ b/"):
            close_hunk()
            current = normalize_path(line[6:])
            continue
        match = HUNK_HEADER_RE.match(line)
        if match:
            close_hunk()
            hunk_start = int(match.group(2))
            new_line = hunk_start - 1
            in_hunk = True
            continue
        if not in_hunk or current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("\\"):
            continue
        else:
            # context line (leading space) or empty
            new_line += 1
    close_hunk()
    return ranges


def expand_ranges(
    ranges: list[tuple[int, int]],
    *,
    nearby: int = NEARBY_LINES,
    file_line_count: int,
) -> list[tuple[int, int]]:
    if not ranges:
        return []
    expanded = [
        (max(1, start - nearby), min(file_line_count, end + nearby))
        for start, end in ranges
        if start > 0
    ]
    expanded.sort()
    merged: list[tuple[int, int]] = []
    for start, end in expanded:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def slice_nearby_context(
    path: str,
    content: str,
    ranges: list[tuple[int, int]],
    *,
    nearby: int = NEARBY_LINES,
) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    if not ranges:
        # no hunk info — still cap to head of file, not full dump
        clipped = "\n".join(lines[: min(len(lines), nearby * 4)])
        if len(clipped) > MAX_HUNK_CHARS:
            clipped = clipped[:MAX_HUNK_CHARS] + "\n[truncated]"
        return f"### {path} (file head — no hunk ranges)\n```\n{clipped}\n```"

    windows = expand_ranges(ranges, nearby=nearby, file_line_count=len(lines))
    parts: list[str] = []
    total = 0
    for start, end in windows:
        block = "\n".join(lines[start - 1 : end])
        piece = f"### {path}:{start}-{end}\n```\n{block}\n```"
        if total + len(piece) > MAX_HUNK_CHARS:
            parts.append(f"### {path}:{start}-{end}\n```\n[truncated nearby context]\n```")
            break
        parts.append(piece)
        total += len(piece)
    return "\n\n".join(parts)


def filter_diff_to_paths(diff_text: str, allowed: set[str]) -> str:
    """Keep only unified-diff file sections for allowed paths."""
    if not allowed:
        return "(no reviewable changed files in diff)"
    parts: list[str] = []
    current: list[str] = []
    current_path: str | None = None
    keeping = False

    def flush() -> None:
        nonlocal current, keeping, current_path
        if keeping and current:
            parts.append("\n".join(current))
        current = []
        keeping = False
        current_path = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            current = [line]
            # diff --git a/foo b/foo
            match = re.search(r" b/(.+)$", line)
            current_path = normalize_path(match.group(1)) if match else None
            keeping = bool(current_path and current_path in allowed)
            continue
        if line.startswith("+++ b/"):
            current_path = normalize_path(line[6:])
            keeping = current_path in allowed
            current.append(line)
            continue
        current.append(line)
    flush()

    text = "\n".join(parts).strip()
    if not text:
        # fall back: keep original but note filter
        return "(diff had no sections matching reviewable changed files)"
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[diff truncated by guardrails]"
    return text


# ---------------------------------------------------------------------------
# Secret scan / redact
# ---------------------------------------------------------------------------

def scan_secrets(text: str, *, path: str = "(prompt)") -> list[SecretHit]:
    hits: list[SecretHit] = []
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            excerpt = match.group(0)
            if len(excerpt) > 80:
                excerpt = excerpt[:40] + "…" + excerpt[-20:]
            hits.append(SecretHit(kind=kind, path=path, excerpt=excerpt))
    return hits


def redact_secrets(text: str) -> tuple[str, int]:
    count = 0
    redacted = text
    for _kind, pattern in SECRET_PATTERNS:
        redacted, n = pattern.subn(REDACTION, redacted)
        count += n
    return redacted, count


# ---------------------------------------------------------------------------
# JSON schema (stdlib validator for our fixed shape)
# ---------------------------------------------------------------------------

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "findings", "analyze_notes"],
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "analyze_notes": {"type": "string"},
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
                    "severity": {"enum": list(SEVERITIES)},
                    "explanation": {"type": "string", "minLength": 1},
                    "recommendation": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {
                        "type": "string",
                        "minLength": 3,
                        "description": (
                            "Must cite diff_hunk:path:line, rag chunk header, "
                            "analyze:path:line, or precheck:id"
                        ),
                    },
                },
            },
        },
    },
}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            return [f"{path}: expected object, got {_type_name(instance)}"]
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}))
            for key in instance:
                if key not in allowed:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(_validate(instance[key], subschema, f"{path}.{key}"))
        return errors

    if expected == "array":
        if not isinstance(instance, list):
            return [f"{path}: expected array, got {_type_name(instance)}"]
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(instance):
                errors.extend(_validate(item, item_schema, f"{path}[{i}]"))
        return errors

    if expected == "string":
        if not isinstance(instance, str):
            return [f"{path}: expected string, got {_type_name(instance)}"]
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string shorter than minLength")
        return errors

    if expected == "integer":
        if type(instance) is not int:
            return [f"{path}: expected integer, got {_type_name(instance)}"]
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: integer below minimum")
        return errors

    if expected == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            return [f"{path}: expected number, got {_type_name(instance)}"]
        if "minimum" in schema and float(instance) < schema["minimum"]:
            errors.append(f"{path}: number below minimum")
        if "maximum" in schema and float(instance) > schema["maximum"]:
            errors.append(f"{path}: number above maximum")
        return errors

    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(f"{path}: value not in enum {schema['enum']}")
    return errors


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object from model output (raw or fenced)."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(stripped[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def validate_review_payload(data: dict[str, Any]) -> list[str]:
    return _validate(data, REVIEW_SCHEMA)


def evidence_is_grounded(
    evidence: str,
    *,
    file_path: str,
    line: int,
    diff_text: str,
    retrieved_text: str,
    analyze_text: str,
    allowed_evidence: set[str] | None = None,
) -> bool:
    """True if evidence cites diff/RAG/analyze/precheck material."""
    e = (evidence or "").strip()
    if len(e) < 3:
        return False
    if allowed_evidence and e in allowed_evidence:
        return True
    if e.startswith("precheck:"):
        return True
    if e.startswith("analyze:"):
        # analyze:path:line or raw analyzer snippet
        return file_path in e or file_path in (analyze_text or "")
    if e.startswith("diff_hunk:"):
        # diff_hunk:path:line
        return file_path in e or f"{file_path}:{line}" in e
    if e.startswith("rag:") or e.startswith("chunk:"):
        return e in (retrieved_text or "") or file_path in (retrieved_text or "")
    # Accept path:line citations and literal snippets present in context
    if f"{file_path}:{line}" in e:
        return True
    if file_path in e and str(line) in e:
        return True
    haystacks = (diff_text or "", retrieved_text or "", analyze_text or "")
    if any(e in hay for hay in haystacks if hay):
        return True
    # short unique token from evidence present in diff near the file
    token = e[:80]
    return any(token in hay for hay in haystacks if hay and len(token) >= 8)


def filter_findings(
    data: dict[str, Any],
    *,
    allowed_files: set[str],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    diff_text: str = "",
    retrieved_text: str = "",
    analyze_text: str = "",
    allowed_evidence: set[str] | None = None,
) -> tuple[dict[str, Any], int, int, int]:
    """Drop invalid/low-confidence/ungrounded findings.

    Returns (data, low_conf, invalid, no_evidence).
    """
    findings = data.get("findings")
    if not isinstance(findings, list):
        return data, 0, 0, 0

    kept: list[dict[str, Any]] = []
    low = 0
    invalid = 0
    no_evidence = 0
    for item in findings:
        if not isinstance(item, dict):
            invalid += 1
            continue
        file_path = normalize_path(str(item.get("file", "")))
        try:
            line = int(item.get("line"))
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            invalid += 1
            continue
        severity = item.get("severity")
        explanation = item.get("explanation")
        recommendation = item.get("recommendation")
        evidence = item.get("evidence")
        if (
            not file_path
            or line < 1
            or severity not in SEVERITIES
            or not isinstance(explanation, str)
            or not explanation.strip()
            or not isinstance(recommendation, str)
            or not recommendation.strip()
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            invalid += 1
            continue
        if allowed_files and file_path not in allowed_files:
            invalid += 1
            continue
        if confidence < min_confidence:
            low += 1
            continue
        if not evidence_is_grounded(
            evidence,
            file_path=file_path,
            line=line,
            diff_text=diff_text,
            retrieved_text=retrieved_text,
            analyze_text=analyze_text,
            allowed_evidence=allowed_evidence,
        ):
            no_evidence += 1
            continue
        kept.append(
            {
                "file": file_path,
                "line": line,
                "severity": severity,
                "explanation": explanation.strip(),
                "recommendation": recommendation.strip(),
                "confidence": round(confidence, 3),
                "evidence": evidence.strip(),
                "source": str(item.get("source") or "model"),
                "check_id": str(item.get("check_id") or ""),
            }
        )

    out = dict(data)
    out["findings"] = kept
    return out, low, invalid, no_evidence


def format_review_markdown(data: dict[str, Any]) -> str:
    findings = data.get("findings") or []
    buckets: dict[str, list[dict[str, Any]]] = {
        "blocker": [],
        "should_fix": [],
        "nit": [],
    }
    for finding in findings:
        buckets.get(str(finding.get("severity")), buckets["nit"]).append(finding)

    def section(title: str, items: list[dict[str, Any]]) -> str:
        if not items:
            return f"## {title}\nNone"
        lines = [f"## {title}"]
        for item in items:
            lines.append(
                f"- `{item['file']}:{item['line']}` "
                f"(confidence={item['confidence']:.2f}, "
                f"source={item.get('source', 'model')})\n"
                f"  - {item['explanation']}\n"
                f"  - Recommendation: {item['recommendation']}\n"
                f"  - Evidence: `{item.get('evidence', '')}`"
            )
        return "\n".join(lines)

    parts = [
        f"## Summary\n{data.get('summary', '').strip() or '(empty)'}",
        section("Blockers", buckets["blocker"]),
        section("Should fix", buckets["should_fix"]),
        section("Nits", buckets["nit"]),
        f"## Analyze notes\n{(data.get('analyze_notes') or 'none').strip()}",
    ]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Read-only agent policy
# ---------------------------------------------------------------------------

FORBIDDEN_GIT_MUTATIONS = (
    "gh pr merge",
    "gh pr review --approve",
    "gh pr review -a",
    "git push",
    "git commit",
    "git checkout",
    "git rebase",
    "git reset",
)


def assert_read_only_policy() -> str:
    """Documented policy string injected into prompts / reports."""
    return (
        "GUARDRAIL: This agent is read-only. It must NEVER approve, merge, "
        "push, commit, checkout, rebase, or otherwise modify a PR or branch. "
        "It only produces a review report for humans."
    )


def build_guarded_file_context(
    paths: list[str],
    *,
    diff_text: str,
    file_loader,
    nearby: int = NEARBY_LINES,
) -> str:
    """Load only nearby code around changed hunks for reviewable paths."""
    ranges_map = refine_ranges_from_hunk_body(diff_text)
    parts: list[str] = []
    total = 0
    for path in paths:
        content = file_loader(path)
        if content is None:
            continue
        # strip prior truncation markers from loaders if present
        body = content.replace("\n[file truncated]", "")
        piece = slice_nearby_context(
            path,
            body,
            ranges_map.get(path, []),
            nearby=nearby,
        )
        if not piece:
            continue
        if total + len(piece) > MAX_CONTEXT_CHARS:
            parts.append(f"### {path}\n```\n[nearby context omitted — budget]\n```")
            break
        parts.append(piece)
        total += len(piece)
    return "\n\n".join(parts) if parts else "(no nearby file context for changed files)"
