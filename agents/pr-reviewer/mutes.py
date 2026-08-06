#!/usr/bin/env python3
"""Tiny mute / preference store (YAML subset via stdlib-friendly loader)."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_MUTES_PATH = AGENT_DIR / "mutes.yaml"


@dataclass
class MuteRule:
    id: str
    reason: str = ""
    file_glob: str = "*"
    severity: str = ""
    contains: str = ""
    pattern: str = ""
    check_id: str = ""


def _parse_simple_yaml_mutes(text: str) -> list[dict[str, str]]:
    """Minimal YAML list parser for mutes.yaml (no PyYAML dependency)."""
    rules: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_mutes = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip() == "mutes:":
            in_mutes = True
            continue
        if not in_mutes:
            continue
        if line.lstrip().startswith("- "):
            if current:
                rules.append(current)
            current = {}
            rest = line.lstrip()[2:].strip()
            if rest and ":" in rest:
                key, _, val = rest.partition(":")
                current[key.strip()] = val.strip().strip("\"'")
            continue
        if current is not None and ":" in line:
            key, _, val = line.strip().partition(":")
            current[key.strip()] = val.strip().strip("\"'")
    if current:
        rules.append(current)
    return rules


def load_mute_rules(path: Path = DEFAULT_MUTES_PATH) -> list[MuteRule]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    raw_rules = _parse_simple_yaml_mutes(text)
    rules: list[MuteRule] = []
    for item in raw_rules:
        rid = item.get("id") or "mute"
        rules.append(
            MuteRule(
                id=rid,
                reason=item.get("reason", ""),
                file_glob=item.get("file_glob", "*") or "*",
                severity=item.get("severity", ""),
                contains=item.get("contains", ""),
                pattern=item.get("pattern", ""),
                check_id=item.get("check_id", ""),
            )
        )
    return rules


def _glob_match(path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    # pathlib-style ** support is weak in fnmatch; try common variants
    variants = {pattern}
    if pattern.startswith("**/"):
        variants.add(pattern[3:])
    if "/**/" in pattern:
        variants.add(pattern.replace("/**/", "/"))
        variants.add(pattern.replace("/**/", "/*/"))
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        if path == prefix or path.startswith(prefix + "/"):
            return True
    for candidate in variants:
        if fnmatch.fnmatch(path, candidate):
            return True
    # recursive *.ext under prefix
    if "**/*" in pattern:
        prefix, _, suffix = pattern.partition("**/*")
        if path.startswith(prefix) and fnmatch.fnmatch(path[len(prefix) :], suffix.lstrip("/")):
            return True
        if path.startswith(prefix) and fnmatch.fnmatch(Path(path).name, suffix.lstrip("/")):
            return True
    return False


def finding_is_muted(finding: dict[str, Any], rules: list[MuteRule]) -> str | None:
    """Return mute id if finding matches a rule, else None."""
    file_path = str(finding.get("file", ""))
    severity = str(finding.get("severity", ""))
    text = f"{finding.get('explanation', '')}\n{finding.get('recommendation', '')}"
    check_id = str(finding.get("check_id", ""))

    for rule in rules:
        if rule.file_glob and not _glob_match(file_path, rule.file_glob):
            continue
        if rule.severity and rule.severity != severity:
            continue
        if rule.check_id and rule.check_id != check_id:
            continue
        if rule.contains and rule.contains.lower() not in text.lower():
            continue
        if rule.pattern:
            try:
                if not re.search(rule.pattern, text, re.IGNORECASE):
                    continue
            except re.error:
                continue
        # If rule only has id/reason with default glob, still require some criterion
        if not any(
            [rule.severity, rule.contains, rule.pattern, rule.check_id]
        ) and rule.file_glob in {"*", "**/*"}:
            continue
        return rule.id
    return None


def apply_mutes(
    findings: list[dict[str, Any]],
    rules: list[MuteRule],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    muted: list[dict[str, Any]] = []
    for finding in findings:
        mute_id = finding_is_muted(finding, rules)
        if mute_id:
            clone = dict(finding)
            clone["muted_by"] = mute_id
            muted.append(clone)
        else:
            kept.append(finding)
    return kept, muted
