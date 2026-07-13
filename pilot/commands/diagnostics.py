from __future__ import annotations

import argparse

from pilot.commands.base import Command


class DiagnosticsCommand(Command):
    name = "diagnostics"
    help = "Run health checks for the bench."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--json", action="store_true", help="Print diagnostics as JSON.")

    @classmethod
    def from_args(cls, args: argparse.Namespace, bench):
        return cls(bench, json_output=args.json)

    def __init__(self, bench, json_output: bool = False) -> None:
        self.bench = bench
        self.json_output = json_output

    def run(self) -> None:
        from pilot.core.diagnostics import DiagnosticReport, DiagnosticRunner

        report = DiagnosticReport(self.bench.config.name, DiagnosticRunner(self.bench).run())
        if self.json_output:
            print(report.to_json())
            return
        self._print(report)

    def _print(self, report: DiagnosticReport) -> None:
        print(f"Diagnostics for {report.bench_name}\n")
        current_group = ""
        for check in report.checks:
            if check.group != current_group:
                current_group = check.group
                print(_group_title(current_group))
            print(f"  {_badge(check.status)} {check.name}: {check.detail}")
            if check.hint:
                print(f"      {check.hint}")
        print("\nResult: " + ("issues found" if report.failed else "all checks passed"))


def _group_title(group: str) -> str:
    return f"{group.capitalize()}:"


def _badge(status: str) -> str:
    icons = {"ok": _green("OK"), "warn": _yellow("WARN"), "fail": _red("FAIL"), "skip": _dim("SKIP")}
    return icons.get(status, status.upper())


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m"


def _dim(text: str) -> str:
    return f"\033[90m{text}\033[0m"
