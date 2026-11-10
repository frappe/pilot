"""Case registry: every benchmark the harness knows about."""

from __future__ import annotations

from benchmark import (
    case_admin_api,
    case_audit,
    case_cli,
    case_config,
    case_inventory,
    case_logs,
    case_password,
    case_processes,
    case_site,
    case_tasks,
    case_templates,
    case_validator_steps,
)
from benchmark.harness import BenchmarkCase

_MODULES = (
    case_config,
    case_inventory,
    case_logs,
    case_site,
    case_tasks,
    case_audit,
    case_templates,
    case_processes,
    case_admin_api,
    case_password,
    case_cli,
    case_validator_steps,
)


def all_cases() -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for module in _MODULES:
        cases.extend(module.CASES)
    return cases


def select_cases(group: str | None = None, name: str | None = None) -> list[BenchmarkCase]:
    cases = all_cases()
    if group:
        cases = [case for case in cases if case.group == group]
    if name:
        cases = [case for case in cases if case.name == name]
    return cases


__all__ = ["all_cases", "select_cases"]
