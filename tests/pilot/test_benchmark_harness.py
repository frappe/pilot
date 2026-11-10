from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "scripts" / "benchmark" / "run.py"


def test_harness_runs_a_case_and_writes_results(tmp_path: Path) -> None:
    json_path = tmp_path / "results.json"
    md_path = tmp_path / "results.md"

    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            "--group",
            "config",
            "--case",
            "config.read",
            "--iterations",
            "2",
            "--warmup",
            "1",
            "--quiet",
            "--json",
            str(json_path),
            "--md",
            str(md_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(json_path.read_text())
    assert payload["results"][0]["name"] == "config.read"
    assert payload["results"][0]["measurements"][0]["iterations"] == 2
    assert payload["results"][0]["measurements"][0]["median_ms"] > 0
    assert "config.read" in md_path.read_text()
