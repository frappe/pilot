"""Audit log append and read benchmarks."""

from __future__ import annotations

from benchmark.harness import Case
from pilot.core.bench.audit_log import AuditLog

CASES = [
    Case(
        name="audit.append",
        group="audit",
        description="AuditLog.append(): one JSONL record",
        fn=lambda fixture: AuditLog(fixture.bench()).append(
            "task", {"event": "queued", "command": "clear-cache", "site": "site1.localhost"}
        ),
        setup=lambda fixture: fixture.reset(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="audit.entries",
        group="audit",
        description="AuditLog.entries(): newest-first scan with a limit",
        fn=lambda fixture: AuditLog(fixture.bench()).entries(limit=100),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="audit.csv",
        group="audit",
        description="AuditLog.get_csv(): filter and render matching records as CSV",
        fn=lambda fixture: AuditLog(fixture.bench()).get_csv(limit=100),
        iterations=20,
        warmup=3,
    ),
]
