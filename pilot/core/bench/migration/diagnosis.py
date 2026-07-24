from __future__ import annotations

import re

_EXECUTING = re.compile(r"^\s*Executing (\S+) in ", re.MULTILINE)
_COLUMN = re.compile(r"for column ['\"`](?P<column>[^'\"`]+)['\"`]", re.IGNORECASE)

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
    matches = _EXECUTING.findall(output)
    return matches[-1] if matches else None


def _column(output: str) -> str | None:
    match = _COLUMN.search(output)
    return match.group("column") if match else None


def _classify(output: str) -> str:
    for kind, signatures in _SIGNATURES:
        if any(signature in output for signature in signatures):
            return kind
    return "unknown"
