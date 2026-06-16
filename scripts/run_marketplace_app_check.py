#!/usr/bin/env python3
"""
Clone a marketplace app's repo and run it through scripts/semgrep-rules/
(press's marketplace audit ruleset, plus frappe/semgrep-rules' checks that
aren't already covered there). Exits non-zero if any finding is blocking,
so CI can fail the PR.

Blocking logic ported from press's marketplace_app_audit check: a finding
blocks if its rule metadata sets is_blocking: true, or its Semgrep severity
maps to Critical/Major (ERROR/CRITICAL/HIGH).

Run:
    python3 scripts/run_marketplace_app_check.py <repo-url> <branch>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

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


def clone_app(repo: str, branch: str, target_dir: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", branch, repo, str(target_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not clone {repo}@{branch}: {result.stderr.strip()}")


def run_semgrep(target_dir: Path) -> list[dict]:
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


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: run_marketplace_app_check.py <repo-url> <branch>", file=sys.stderr)
        sys.exit(1)

    repo, branch = sys.argv[1], sys.argv[2]
    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "app"
        clone_app(repo, branch, clone_dir)
        findings = run_semgrep(clone_dir)

    blocking_findings = [finding for finding in findings if is_blocking(finding)]

    print(f"Scanned {repo}@{branch}: {len(findings)} finding(s), {len(blocking_findings)} blocking.\n")
    for finding in findings:
        print_finding(finding)

    if blocking_findings:
        print(f"\nFAILED: {len(blocking_findings)} blocking issue(s) must be fixed before this can be merged.")
        sys.exit(1)

    print("\nPASSED.")


if __name__ == "__main__":
    main()
