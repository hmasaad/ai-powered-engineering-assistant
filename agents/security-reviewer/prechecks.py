#!/usr/bin/env python3
"""Deterministic Flutter security prechecks (before the LLM).

Findings are high-confidence and evidence-bound (`precheck:<id>`).
The model should focus on residual security risk not already covered.

Note: prompt secret *redaction* lives in pr-reviewer guardrails. These
prechecks turn secret-like / insecure patterns into review findings.
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


def _line_at(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def parse_analyze_for_paths(
    analyze_text: str,
    reviewed_paths: list[str],
) -> list[PrecheckFinding]:
    """Surface analyzer hits on changed files that often correlate with security."""
    findings: list[PrecheckFinding] = []
    path_set = set(reviewed_paths)
    pattern = re.compile(
        r"^\s*(error|warning|info)\s+[•·-]\s+(.+?)\s+[•·-]\s+"
        r"([^:]+):(\d+):\d+",
        re.IGNORECASE | re.MULTILINE,
    )
    security_keywords = (
        "security",
        "unsafe",
        "insecure",
        "permission",
        "certificate",
        "ssl",
        "tls",
        "crypto",
        "password",
        "secret",
        "token",
        "auth",
        "deprecated",
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
        if not any(k in msg.lower() for k in security_keywords) and level.lower() != "error":
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
                recommendation=(
                    "Fix the analyzer issue; security-related analyzer hits "
                    "often map to trust or crypto misuse."
                ),
                confidence=0.9 if level_l == "error" else 0.8,
                evidence=f"analyze:{path}:{line_s}",
                check_id="flutter_analyze_security",
            )
        )
    return findings


def check_hardcoded_secrets(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    """Flag secret-like assignments and well-known key prefixes in source."""
    findings: list[PrecheckFinding] = []
    patterns: list[tuple[str, re.Pattern[str], str, float]] = [
        (
            "hardcoded_secret_assignment",
            re.compile(
                r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
                r"client[_-]?secret|private[_-]?key|password|passwd)\b\s*[:=]\s*"
                r"['\"][^'\"]{8,}['\"]"
            ),
            "blocker",
            0.92,
        ),
        (
            "google_api_key_literal",
            re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
            "blocker",
            0.95,
        ),
        (
            "aws_access_key_literal",
            re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
            "blocker",
            0.95,
        ),
        (
            "private_key_block",
            re.compile(
                r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
            ),
            "blocker",
            0.98,
        ),
        (
            "bearer_token_literal",
            re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
            "should_fix",
            0.85,
        ),
    ]
    for path in paths:
        if not path.endswith((".dart", ".json", ".yaml", ".yml", ".env", ".md")):
            continue
        # Skip agent rule docs that mention patterns as examples
        if path.startswith("agents/") and path.endswith(".md"):
            continue
        content = loader(path)
        if not content:
            continue
        for check_id, pattern, severity, confidence in patterns:
            for match in pattern.finditer(content):
                findings.append(
                    PrecheckFinding(
                        file=path,
                        line=_line_at(content, match.start()),
                        severity=severity,
                        explanation=(
                            "Hardcoded secret-like credential detected in source. "
                            "Committed secrets can be mined from git history."
                        ),
                        recommendation=(
                            "Remove the secret, rotate it, and load credentials from "
                            "secure storage / environment / a secrets manager instead."
                        ),
                        confidence=confidence,
                        evidence=f"precheck:{check_id}:{path}",
                        check_id=check_id,
                    )
                )
    return findings


def check_cleartext_http(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    pattern = re.compile(r"""['"]http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)[^'"]+['"]""")
    for path in paths:
        if not path.endswith(".dart"):
            continue
        if "/api/" not in path and "/services/" not in path and "/core/" not in path:
            # Still check UI that builds absolute URLs
            if "/ui/" not in path and "/blocs/" not in path:
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
                    explanation=(
                        "Cleartext `http://` endpoint (non-loopback) can expose "
                        "credentials and booking data to network observers."
                    ),
                    recommendation=(
                        "Use `https://` for remote endpoints. Keep cleartext only "
                        "for explicit local/dev hosts behind a debug guard."
                    ),
                    confidence=0.88,
                    evidence=f"precheck:cleartext_http:{path}",
                    check_id="cleartext_http",
                )
            )
    return findings


def check_insecure_storage(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    prefs = re.compile(r"\bSharedPreferences\b")
    sensitive_write = re.compile(
        r"""\.(?:setString|setStringList)\s*\(\s*['"][^'"]*(?:token|secret|password|passwd|api[_-]?key|auth|credential)[^'"]*['"]""",
        re.IGNORECASE,
    )
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        if not prefs.search(content):
            continue
        for match in sensitive_write.finditer(content):
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="should_fix",
                    explanation=(
                        "Sensitive value written via SharedPreferences; prefs are "
                        "not encrypted and can be read on a compromised device."
                    ),
                    recommendation=(
                        "Store tokens/secrets with flutter_secure_storage (or "
                        "platform keychain/keystore), not SharedPreferences."
                    ),
                    confidence=0.9,
                    evidence=f"precheck:insecure_prefs_secret:{path}",
                    check_id="insecure_prefs_secret",
                )
            )
    return findings


def check_sensitive_logging(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    log_call = re.compile(
        r"\b(?:print|debugPrint|log)\s*\(([^)]{0,200})\)",
        re.MULTILINE,
    )
    sensitive = re.compile(
        r"(?i)\b(password|passwd|token|secret|authorization|api[_-]?key|bearer|credential|ssn|email)\b"
    )
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        for match in log_call.finditer(content):
            args = match.group(1)
            if not sensitive.search(args):
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="should_fix",
                    explanation=(
                        "Logging appears to include sensitive fields (token/password/"
                        "PII). Logs can leak via crash reporters and device consoles."
                    ),
                    recommendation=(
                        "Remove or redact sensitive fields before logging; never log "
                        "raw tokens, passwords, or authorization headers."
                    ),
                    confidence=0.84,
                    evidence=f"precheck:sensitive_logging:{path}",
                    check_id="sensitive_logging",
                )
            )
    return findings


def check_tls_bypass(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    patterns: list[tuple[str, re.Pattern[str], str]] = [
        (
            "bad_certificate_callback",
            re.compile(r"badCertificateCallback\s*:"),
            "blocker",
        ),
        (
            "allow_bad_certificates",
            re.compile(r"(?i)allowBadCertificates\s*[:=]\s*true"),
            "blocker",
        ),
        (
            "http_overrides_insecure",
            re.compile(r"\bHttpOverrides\b"),
            "should_fix",
        ),
    ]
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        for check_id, pattern, severity in patterns:
            for match in pattern.finditer(content):
                findings.append(
                    PrecheckFinding(
                        file=path,
                        line=_line_at(content, match.start()),
                        severity=severity,
                        explanation=(
                            "TLS/certificate validation appears weakened or overridden, "
                            "enabling man-in-the-middle attacks."
                        ),
                        recommendation=(
                            "Remove certificate bypasses from production paths. Use "
                            "proper CA trust or pinning only with a deliberate, reviewed design."
                        ),
                        confidence=0.93 if severity == "blocker" else 0.8,
                        evidence=f"precheck:{check_id}:{path}",
                        check_id=check_id,
                    )
                )
    return findings


def check_webview_risks(
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
        if "WebView" not in content and "InAppWebView" not in content:
            continue
        for match in re.finditer(
            r"JavascriptMode\s*\.\s*unrestricted|javaScriptEnabled\s*:\s*true",
            content,
        ):
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="should_fix",
                    explanation=(
                        "WebView enables unrestricted JavaScript; untrusted content "
                        "can escalate to XSS-like bridge attacks."
                    ),
                    recommendation=(
                        "Disable JS unless required; restrict navigation delegates; "
                        "never expose sensitive Dart bridges to untrusted pages."
                    ),
                    confidence=0.78,
                    evidence=f"precheck:webview_js_unrestricted:{path}",
                    check_id="webview_js_unrestricted",
                )
            )
        for match in re.finditer(
            r"(?i)(?:allowFileAccess|allowFileAccessFromFileURLs|allowUniversalAccessFromFileURLs)\s*:\s*true",
            content,
        ):
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="blocker",
                    explanation=(
                        "WebView file-access flags are enabled; local file URLs can "
                        "exfiltrate app sandbox data."
                    ),
                    recommendation=(
                        "Keep file-access WebView flags false unless strictly needed "
                        "and the loaded content is fully trusted."
                    ),
                    confidence=0.9,
                    evidence=f"precheck:webview_file_access:{path}",
                    check_id="webview_file_access",
                )
            )
    return findings


def check_unvalidated_url_launch(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    launch = re.compile(r"\blaunchUrl\s*\(")
    for path in paths:
        if not path.endswith(".dart"):
            continue
        content = loader(path)
        if not content:
            continue
        for match in launch.finditer(content):
            window = content[match.start() : match.start() + 400]
            # Soft signal: launching a variable URL without an obvious allowlist/scheme check nearby
            if re.search(r"canLaunchUrl|LaunchMode|httpsOnly|allowList|scheme", window):
                continue
            if re.search(r"Uri\.parse\s*\(\s*['\"]https://", window):
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="nit",
                    explanation=(
                        "URL launch without an obvious scheme/allowlist check nearby; "
                        "attacker-controlled links can open unexpected handlers."
                    ),
                    recommendation=(
                        "Validate scheme (https) and host against an allowlist before "
                        "calling launchUrl; prefer LaunchMode.externalApplication carefully."
                    ),
                    confidence=0.7,
                    evidence=f"precheck:unvalidated_url_launch:{path}",
                    check_id="unvalidated_url_launch",
                )
            )
    return findings


def check_debug_security_bypass(
    paths: list[str],
    diff_text: str,
) -> list[PrecheckFinding]:
    """Flag added lines that skip auth/security behind kDebugMode / assert."""
    findings: list[PrecheckFinding] = []
    current: str | None = None
    new_line = 0
    risky = re.compile(
        r"(?i)\b(kDebugMode|assert\s*\()\b.*\b(skip|bypass|ignore).*(auth|security|ssl|tls|cert)|"
        r"\b(skipAuth|bypassAuth|disableSecurity|ignoreBadCertificate)\b"
    )
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
            if risky.search(body):
                findings.append(
                    PrecheckFinding(
                        file=current,
                        line=new_line,
                        severity="should_fix",
                        explanation=(
                            "Diff adds a debug/assert security bypass. These often "
                            "leak into release builds or weaken local trust assumptions."
                        ),
                        recommendation=(
                            "Keep bypasses out of shared code paths; gate with compile-time "
                            "flags that cannot ship in release, or remove entirely."
                        ),
                        confidence=0.8,
                        evidence=f"diff_hunk:{current}:{new_line}",
                        check_id="debug_security_bypass",
                    )
                )
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("\\"):
            continue
        else:
            new_line += 1
    return findings


def check_path_traversal(
    paths: list[str],
    loader: FileLoader,
) -> list[PrecheckFinding]:
    findings: list[PrecheckFinding] = []
    risky = re.compile(
        r"""File\s*\(\s*[^)]*(?:\+|\$\{|\w+\s*\+)[^)]*\)|"""
        r"""Directory\s*\(\s*[^)]*(?:\+|\$\{)[^)]*\)"""
    )
    for path in paths:
        if not path.endswith(".dart"):
            continue
        if "/api/" not in path and "/services/" not in path and "/core/" not in path:
            continue
        content = loader(path)
        if not content:
            continue
        for match in risky.finditer(content):
            window = content[match.start() : match.start() + 200]
            if "path.normalize" in window or "canonicalize" in window or "p.join" in window:
                continue
            findings.append(
                PrecheckFinding(
                    file=path,
                    line=_line_at(content, match.start()),
                    severity="nit",
                    explanation=(
                        "File/Directory path built via string concatenation; untrusted "
                        "input can escape intended directories."
                    ),
                    recommendation=(
                        "Build paths with path.join / package:path, then verify the "
                        "resolved path stays under an allowlisted base directory."
                    ),
                    confidence=0.72,
                    evidence=f"precheck:path_concat:{path}",
                    check_id="path_concat",
                )
            )
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
    findings.extend(check_hardcoded_secrets(reviewed_paths, loader))
    findings.extend(check_cleartext_http(reviewed_paths, loader))
    findings.extend(check_insecure_storage(reviewed_paths, loader))
    findings.extend(check_sensitive_logging(reviewed_paths, loader))
    findings.extend(check_tls_bypass(reviewed_paths, loader))
    findings.extend(check_webview_risks(reviewed_paths, loader))
    findings.extend(check_unvalidated_url_launch(reviewed_paths, loader))
    findings.extend(check_debug_security_bypass(reviewed_paths, diff_text))
    findings.extend(check_path_traversal(reviewed_paths, loader))

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
        return "(no deterministic security precheck findings)"
    lines = [
        "These security issues were already detected deterministically. "
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
