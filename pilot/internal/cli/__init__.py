"""Internal console entrypoint and argparse plumbing."""

from __future__ import annotations

from pilot.internal.cli.dispatch import (
    CliContext,
    build_context,
    find_bench_root,
    forwarded_frappe_args,
    is_frappe_passthrough,
    main,
    strip_bench_flag,
)

__all__ = [
    "CliContext",
    "build_context",
    "find_bench_root",
    "forwarded_frappe_args",
    "is_frappe_passthrough",
    "main",
    "strip_bench_flag",
]
