"""Tests for the console entrypoint and internal CLI dispatch."""

from __future__ import annotations

import subprocess
import sys

import pytest

import pilot.cli as cli
import pilot.internal.cli.dispatch as dispatch
import pilot.internal.cli.registry as registry


def test_cli_module_is_only_the_console_entrypoint() -> None:
    assert cli.__all__ == ["main"]
    assert cli.main is dispatch.main
    assert not hasattr(cli, "strip_bench_flag")
    assert not hasattr(cli, "is_frappe_passthrough")


def test_strip_bench_flag_long_form() -> None:
    bench_name, remaining = dispatch.strip_bench_flag(["--bench", "my-bench", "start"])
    assert bench_name == "my-bench"
    assert remaining == ["start"]


def test_strip_bench_flag_short_form() -> None:
    bench_name, remaining = dispatch.strip_bench_flag(["-b", "my-bench", "start"])
    assert bench_name == "my-bench"
    assert remaining == ["start"]


def test_strip_bench_flag_equals_form() -> None:
    bench_name, remaining = dispatch.strip_bench_flag(["--bench=my-bench", "start"])
    assert bench_name == "my-bench"
    assert remaining == ["start"]


def test_strip_bench_flag_short_equals_form() -> None:
    bench_name, remaining = dispatch.strip_bench_flag(["-b=my-bench", "stop"])
    assert bench_name == "my-bench"
    assert remaining == ["stop"]


def test_strip_bench_flag_no_bench_flag() -> None:
    bench_name, remaining = dispatch.strip_bench_flag(["start", "--verbose"])
    assert bench_name is None
    assert remaining == ["start", "--verbose"]


def test_strip_bench_flag_preserves_frappe_sub_options() -> None:
    """--site and other frappe sub-options must survive stripping."""
    bench_name, remaining = dispatch.strip_bench_flag(
        ["-b", "my-bench", "frappe", "--site", "s.localhost", "migrate"]
    )
    assert bench_name == "my-bench"
    assert remaining == ["frappe", "--site", "s.localhost", "migrate"]


def test_strip_bench_flag_empty_args() -> None:
    bench_name, remaining = dispatch.strip_bench_flag([])
    assert bench_name is None
    assert remaining == []


def test_passthrough_own_commands_are_not_forwarded() -> None:
    for cmd in ("start", "stop", "init", "new", "get-app", "new-site", "build", "update"):
        assert not dispatch.is_frappe_passthrough(
            [cmd]
        ), f"{cmd!r} should not be a passthrough"


def test_passthrough_unknown_commands_are_forwarded() -> None:
    assert dispatch.is_frappe_passthrough(["--site", "s.localhost", "migrate"]) is True


def test_passthrough_bench_flag_does_not_trigger_passthrough() -> None:
    assert dispatch.is_frappe_passthrough(["--bench", "my-bench", "start"]) is False


def test_passthrough_inline_bench_flag_does_not_trigger_passthrough() -> None:
    assert dispatch.is_frappe_passthrough(["--bench=my-bench", "start"]) is False


def test_passthrough_unknown_inline_option_is_forwarded() -> None:
    assert dispatch.is_frappe_passthrough(["--verbose=1", "migrate"]) is True


def test_passthrough_empty_args() -> None:
    assert dispatch.is_frappe_passthrough([]) is False


def test_discovery_does_not_import_heavy_layers() -> None:
    """Command discovery does not import heavy manager/core/config layers."""
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
import pilot.internal.cli.registry as r; r._discover()
builtins.__import__ = _orig
print('\\n'.join(_leaks))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    leaked = result.stdout.strip()
    assert leaked == "", f"discovery imported heavy layers at import time:\n{leaked}"


def test_command_discovery_matches_baseline() -> None:
    commands = registry._discover()
    identities = {(command.group, command.name) for command in commands}

    assert len(commands) == 33
    assert len(identities) == 33
    assert registry.command_names() == {
        "build",
        "build-admin",
        "drop",
        "frappe",
        "generate-admin-session",
        "get-app",
        "init",
        "install-app",
        "issue-site-token",
        "list-apps",
        "list-site-apps",
        "ls",
        "new",
        "new-site",
        "remove",
        "remove-app",
        "rename-site",
        "restart",
        "set-admin-password",
        "set-central-config",
        "setup",
        "start",
        "stop",
        "tasks",
        "uninstall-app",
        "update",
        "upgrade",
    }
    assert {name for group, name in identities if group == "remove"} == {"production"}
    assert {name for group, name in identities if group == "setup"} == {
        "config",
        "letsencrypt",
        "nginx",
        "production",
        "requirements",
    }
    assert {name for group, name in identities if group == "tasks"} == {
        "start",
        "status",
        "stop",
    }


def test_main_dispatches_native_command(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched = []
    monkeypatch.setattr(sys, "argv", ["bench", "--bench", "demo", "restart"])
    monkeypatch.setattr(
        registry,
        "dispatch",
        lambda args, parser, context: dispatched.append((args.command, context.bench_name)),
    )

    cli.main()

    assert dispatched == [("restart", "demo")]


def test_main_forwards_unknown_command_to_frappe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        dispatch,
        "run_frappe",
        lambda context, args: calls.append((context.bench_name, args, context.verbose)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["bench", "--bench", "demo", "--site", "site.localhost", "migrate"],
    )

    cli.main()

    assert calls == [("demo", ["--site", "site.localhost", "migrate"], False)]


def test_main_forwards_explicit_frappe_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        dispatch,
        "run_frappe",
        lambda context, args: calls.append((context.bench_name, args, context.verbose)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["bench", "-b", "demo", "frappe", "--site", "site.localhost", "migrate", "--verbose"],
    )

    cli.main()

    assert calls == [("demo", ["--site", "site.localhost", "migrate", "--verbose"], True)]


def test_main_dispatches_all_benches_without_selecting_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched = []
    monkeypatch.setattr(sys, "argv", ["bench", "-b", "all", "restart"])
    monkeypatch.setattr(
        registry,
        "dispatch_all",
        lambda args, parser, context: dispatched.append((args.command, context.bench_name)),
    )

    cli.main()

    assert dispatched == [("restart", "all")]
