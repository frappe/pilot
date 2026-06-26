"""Tests for pilot.cli — argument parsing helpers."""
from __future__ import annotations

import subprocess
import sys

import pytest

from pilot.cli import _strip_bench_flag, _is_frappe_passthrough


# ── _strip_bench_flag ─────────────────────────────────────────────────────────


def test_strip_bench_flag_long_form() -> None:
    bench_name, remaining = _strip_bench_flag(["--bench", "my-bench", "start"])
    assert bench_name == "my-bench"
    assert remaining == ["start"]


def test_strip_bench_flag_short_form() -> None:
    bench_name, remaining = _strip_bench_flag(["-b", "my-bench", "start"])
    assert bench_name == "my-bench"
    assert remaining == ["start"]


def test_strip_bench_flag_equals_form() -> None:
    bench_name, remaining = _strip_bench_flag(["--bench=my-bench", "start"])
    assert bench_name == "my-bench"
    assert remaining == ["start"]


def test_strip_bench_flag_short_equals_form() -> None:
    bench_name, remaining = _strip_bench_flag(["-b=my-bench", "stop"])
    assert bench_name == "my-bench"
    assert remaining == ["stop"]


def test_strip_bench_flag_no_bench_flag() -> None:
    bench_name, remaining = _strip_bench_flag(["start", "--verbose"])
    assert bench_name is None
    assert remaining == ["start", "--verbose"]


def test_strip_bench_flag_preserves_frappe_sub_options() -> None:
    """--site and other frappe sub-options must survive stripping."""
    bench_name, remaining = _strip_bench_flag(
        ["-b", "my-bench", "frappe", "--site", "s.localhost", "migrate"]
    )
    assert bench_name == "my-bench"
    assert remaining == ["frappe", "--site", "s.localhost", "migrate"]


def test_strip_bench_flag_empty_args() -> None:
    bench_name, remaining = _strip_bench_flag([])
    assert bench_name is None
    assert remaining == []


# ── _is_frappe_passthrough ────────────────────────────────────────────────────


def test_passthrough_own_commands_are_not_forwarded() -> None:
    for cmd in ("start", "stop", "init", "new", "get-app", "new-site", "build", "update"):
        assert _is_frappe_passthrough([cmd]) is False, f"{cmd!r} should not be a passthrough"


def test_passthrough_unknown_commands_are_forwarded() -> None:
    assert _is_frappe_passthrough(["--site", "s.localhost", "migrate"]) is True


def test_passthrough_bench_flag_does_not_trigger_passthrough() -> None:
    assert _is_frappe_passthrough(["--bench", "my-bench", "start"]) is False


def test_passthrough_empty_args() -> None:
    assert _is_frappe_passthrough([]) is False


# ── discovery stays light ─────────────────────────────────────────────────────


def test_discovery_does_not_import_heavy_layers() -> None:
    """Command discovery imports every module under pilot/commands/, so command
    modules must keep their managers/core/config imports scoped to point of use.
    If any of those heavy layers loads at discovery time, CLI startup grows with
    every command added. Run in a clean interpreter so other tests' imports don't
    pollute sys.modules.
    """
    # Patch __import__ to record the pilot call-site responsible for each
    # heavy import: (leaked_module, importer_file, importer_line).
    code = """
import builtins, sys, traceback
_orig = builtins.__import__
_leaks = []
_HEAVY = ('pilot.managers', 'pilot.core', 'pilot.config')

def _tracing_import(name, *args, **kwargs):
    already_loaded = name in sys.modules
    result = _orig(name, *args, **kwargs)
    if name.startswith(_HEAVY) and not already_loaded:
        frames = [f for f in traceback.extract_stack()[:-1] if 'pilot' in (f.filename or '')]
        if frames:
            f = frames[-1]
            _leaks.append(f"{name}  <-  {f.filename}:{f.lineno}")
    return result

builtins.__import__ = _tracing_import
import pilot.registry as r; r._discover()
builtins.__import__ = _orig
print('\\n'.join(_leaks))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    leaked = result.stdout.strip()
    assert leaked == "", f"discovery imported heavy layers at import time:\n{leaked}"
