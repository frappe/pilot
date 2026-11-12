from __future__ import annotations

import re

# (failure_kind, substring signatures) ordered most-specific first.
_SIGNATURES: list[tuple[str, tuple[str, ...]]] = [
    ("string_to_number", ("Incorrect integer value", "Incorrect decimal value", "Truncated incorrect")),
    ("data_truncated", ("Data truncated for column",)),
    ("unknown_column", ("Unknown column",)),
    ("duplicate_entry", ("Duplicate entry",)),
    ("missing_table", ("doesn't exist", "Table '")),
]


def diagnose(output: str, message: str, database_engine: str = "mariadb") -> dict:
    """Classify a failed migrate run; unrecognized failures stay null for manual repair."""
    output = output or ""
    return {
        "phase": "migrate",
        "patch": _last_patch(output),
        "table": None,
        "column": _column(output),
        "database_engine": database_engine,
        "failure_kind": _classify(output),
        "message": message,
        "output_excerpt": output[-4000:],
        "resolver_id": None,
    }


def _last_patch(output: str) -> str | None:
    """The last patch the migrate output started, if any."""
    matches = re.findall(r"^\s*Executing (\S+) in ", output, flags=re.MULTILINE)
    return matches[-1] if matches else None


def _column(output: str) -> str | None:
    """The column a migrate error names, if any."""
    match = re.search(r"for column ['\"`](?P<column>[^'\"`]+)['\"`]", output, flags=re.IGNORECASE)
    return match.group("column") if match else None


def _classify(output: str) -> str:
    """The failure kind whose signature appears in the output, or "unknown"."""
    for kind, signatures in _SIGNATURES:
        if any(signature in output for signature in signatures):
            return kind
    return "unknown"
