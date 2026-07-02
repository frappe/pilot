#!/usr/bin/env python3
"""
Base class for marketplace app validators.

Each validator collects failures via fail() while validate() runs, then
run() reports an overall pass/fail. Subclasses take only the inputs they
need and implement validate().
"""

from __future__ import annotations


class Validator:
    name = "validation"

    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, message: str) -> None:
        self.errors.append(message)
        print(f"  FAIL: {message}")

    def validate(self) -> None:
        raise NotImplementedError

    def run(self) -> bool:
        print(f"\n--- {self.name} ---", flush=True)
        self.validate()
        if self.errors:
            print(f"  {len(self.errors)} issue(s) found.")
        else:
            print("  PASSED.")
        return not self.errors
