#!/usr/bin/env python3
"""
Clone a marketplace app's repo and run it through scripts/semgrep-rules/.
Exits non-zero if any finding is blocking, so CI can fail the PR.

Blocking logic: a finding blocks if its rule metadata sets is_blocking: true,
or its Semgrep severity maps to Critical/Major (ERROR/CRITICAL/HIGH).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validator import Validator

RULES_DIR = Path(__file__).parent / "semgrep-rules"

SEMGREP_TO_AUDIT_SEVERITY = {
    "CRITICAL": "Critical",
    "ERROR": "Critical",
    "HIGH": "Major",
    "WARNING": "Minor",
    "MEDIUM": "Minor",
    "LOW": "Info",
    "INFO": "Info",
}
BLOCKING_AUDIT_SEVERITIES = {"Critical", "Major"}


def scan_target(target_dir: Path) -> list[dict]:
    result = subprocess.run(
        ["semgrep", "scan", "--config", str(RULES_DIR), "--json", "--quiet", str(target_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Semgrep failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["results"]


def is_blocking(finding: dict) -> bool:
    metadata = finding.get("extra", {}).get("metadata", {})
    if metadata.get("is_blocking") is True:
        return True
    severity = str(finding.get("extra", {}).get("severity", "INFO")).upper()
    return SEMGREP_TO_AUDIT_SEVERITY.get(severity, "Info") in BLOCKING_AUDIT_SEVERITIES


def print_finding(finding: dict) -> None:
    extra = finding.get("extra", {})
    message = " ".join(extra.get("message", "").split())
    line = finding.get("start", {}).get("line")
    print(f"  [{extra.get('severity')}] {finding['path']}:{line} ({finding['check_id']})")
    print(f"    {message}")


class SemgrepValidator(Validator):
    name = "semgrep scan"

    def __init__(self, clone_dir: Path, label: str):
        super().__init__()
        self.clone_dir = clone_dir
        self.label = label

    def validate(self) -> None:
        findings = scan_target(self.clone_dir)
        blocking = [f for f in findings if is_blocking(f)]
        print(f"  Scanned {self.label}: {len(findings)} finding(s), {len(blocking)} blocking.")
        for finding in findings:
            print_finding(finding)
        for finding in blocking:
            severity = finding.get("extra", {}).get("severity")
            self.fail(f"[{severity}] {finding['path']} ({finding['check_id']})")
