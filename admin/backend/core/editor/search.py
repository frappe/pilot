from __future__ import annotations

import json
import re
import subprocess

_MATCH_CAP = 2000
_SED_DELIM = "\x01"


def search(root, query: str, regex: bool, word: bool, case: bool) -> list[dict]:
    """Project-wide ripgrep search, grouped by file."""
    if not query:
        return []
    args = ["--json", "--max-count", "500", "--max-columns", "500", "--max-filesize", "2M"]
    if not regex:
        args.append("--fixed-strings")
    if word:
        args.append("--word-regexp")
    args.append("--case-sensitive" if case else "--ignore-case")
    args += ["--", query]

    try:
        proc = subprocess.run(
            ["rg", *args], cwd=root, text=True, capture_output=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return []

    results: list[dict] = []
    by_file: dict[str, int] = {}
    total = 0
    for raw in proc.stdout.split("\n"):
        if total >= _MATCH_CAP or not raw:
            break
        if _consume_match(raw, results, by_file):
            total += 1
    return results


def _consume_match(raw: str, results: list[dict], by_file: dict[str, int]) -> bool:
    try:
        event = json.loads(raw)
    except ValueError:
        return False
    if event.get("type") != "match":
        return False
    data = event["data"]
    path = data["path"]["text"]
    if path not in by_file:
        results.append({"file": path, "matches": []})
        by_file[path] = len(results) - 1
    submatches = data.get("submatches") or [{}]
    results[by_file[path]]["matches"].append(
        {
            "line": data["line_number"],
            "text": data["lines"]["text"],
            "start": submatches[0].get("start", 0),
            "end": submatches[0].get("end", 0),
        }
    )
    return True


def replace(workspace, query: str, replacement: str, regex: bool, case: bool, word: bool, files: list[str]) -> int:
    """Replace across the given files with sed; returns the count of files changed."""
    pattern = _sed_escape(query, literal=not regex, is_pattern=True)
    if word:
        pattern = rf"\b{pattern}\b"
    rep = _sed_escape(replacement, literal=not regex, is_pattern=False)
    flags = "g" if case else "gI"
    script = f"s{_SED_DELIM}{pattern}{_SED_DELIM}{rep}{_SED_DELIM}{flags}"

    changed = 0
    for name in files:
        target = workspace.safe(name)
        try:
            src = target.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            out = subprocess.run(
                ["sed", "-E", script], input=src, text=True, capture_output=True, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if out != src:
            target.write_text(out, "utf-8")
            changed += 1
    return changed


def _sed_escape(text: str, literal: bool, is_pattern: bool) -> str:
    out = []
    for ch in text:
        if ch == _SED_DELIM:
            out.append("\\" + ch)
            continue
        if literal:
            if is_pattern:
                out.append(re.escape(ch))
                continue
            if ch in ("\\", "&"):
                out.append("\\")
        out.append(ch)
    return "".join(out)
