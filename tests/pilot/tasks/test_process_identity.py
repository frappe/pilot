from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pilot.tasks.manager.process_identity import (
    ProcessInspector,
    ProcessOwnership,
)


@pytest.fixture
def owned_process():
    launch_id = "test-launch-id"
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    process = subprocess.Popen(
        argv,
        start_new_session=True,
        env={**os.environ, "BENCH_TASK_LAUNCH_ID": launch_id},
    )
    try:
        yield process, argv, launch_id
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def test_process_identity_round_trips_owned_group(owned_process) -> None:
    process, argv, launch_id = owned_process
    inspector = ProcessInspector()

    identity = inspector.capture(process.pid, argv, launch_id)

    assert inspector.inspect(identity, argv) == ProcessOwnership.OWNED
    assert type(identity).from_dict(identity.to_dict()) == identity


def test_capture_waits_for_child_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    inspector = ProcessInspector()
    argv = [sys.executable, "-c", "pass"]
    expected_hash = inspector._argv_hash(argv)
    snapshots = iter(
        [
            SimpleNamespace(
                state="R",
                pgid=123,
                sid=123,
                start_ticks=1,
                uid=os.getuid(),
                argv_hash="pre-exec-command",
            ),
            SimpleNamespace(
                state="R",
                pgid=123,
                sid=123,
                start_ticks=1,
                uid=os.getuid(),
                argv_hash=expected_hash,
            ),
        ]
    )
    monkeypatch.setattr(inspector, "_read_process", lambda pid: next(snapshots))
    monkeypatch.setattr(inspector, "_has_launch_id", lambda pid, launch_id: True)
    monkeypatch.setattr(inspector, "_read_boot_id", lambda: "boot")
    monkeypatch.setattr("pilot.tasks.manager.process_identity.time.sleep", lambda _: None)

    identity = inspector.capture(123, argv, "launch")

    assert identity.argv_hash == expected_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("boot_id", "another-boot"),
        ("start_ticks", -1),
        ("uid", -1),
        ("pgid", -1),
        ("sid", -1),
        ("argv_hash", "wrong"),
        ("launch_id", "wrong"),
    ],
)
def test_process_identity_rejects_mismatched_ownership(
    owned_process,
    field: str,
    value,
) -> None:
    process, argv, launch_id = owned_process
    inspector = ProcessInspector()
    identity = replace(inspector.capture(process.pid, argv, launch_id), **{field: value})

    assert inspector.inspect(identity, argv) == ProcessOwnership.STALE


def test_missing_process_identity_is_dead() -> None:
    argv = [sys.executable, "-c", "pass"]
    inspector = ProcessInspector()
    process = subprocess.Popen(
        argv,
        start_new_session=True,
        env={**os.environ, "BENCH_TASK_LAUNCH_ID": "gone"},
    )
    identity = inspector.capture(process.pid, argv, "gone")
    process.wait(timeout=5)

    assert inspector.inspect(identity, argv) == ProcessOwnership.DEAD


def test_live_descendant_keeps_owned_group_after_leader_exits() -> None:
    launch_id = "orphan-launch"
    argv = [
        sys.executable,
        "-c",
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "time.sleep(0.2)",
    ]
    leader = subprocess.Popen(
        argv,
        start_new_session=True,
        env={**os.environ, "BENCH_TASK_LAUNCH_ID": launch_id},
    )
    inspector = ProcessInspector()
    identity = inspector.capture(leader.pid, argv, launch_id)

    try:
        leader.wait(timeout=5)
        assert inspector.inspect(identity, argv) == ProcessOwnership.OWNED
    finally:
        try:
            os.killpg(identity.pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_permission_failure_is_unknown(
    owned_process,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, argv, launch_id = owned_process
    inspector = ProcessInspector()
    identity = inspector.capture(process.pid, argv, launch_id)
    monkeypatch.setattr(inspector, "_read_process", lambda pid: (_ for _ in ()).throw(PermissionError()))

    assert inspector.inspect(identity, argv) == ProcessOwnership.UNKNOWN


def test_different_expected_arguments_are_stale(owned_process) -> None:
    process, argv, launch_id = owned_process
    inspector = ProcessInspector()
    identity = inspector.capture(process.pid, argv, launch_id)

    assert inspector.inspect(identity, [*argv, "other"]) == ProcessOwnership.STALE
