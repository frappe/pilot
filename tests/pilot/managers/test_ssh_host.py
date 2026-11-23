from __future__ import annotations

import shlex
import subprocess
from unittest.mock import patch

import pytest

from pilot.exceptions import CommandError
from pilot.managers.ssh_host import SSHHost

MODULE = "pilot.managers.ssh_host"


def _host() -> SSHHost:
    return SSHHost("fdaa:1:2::3", "ubuntu", "/home/pilot/.ssh/database")


def _completed(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout.encode(), b"")


def _remote_argv(command: list[str]) -> list[str]:
    assert command[-2] == "--"
    return shlex.split(command[-1])


def test_command_logs_in_with_the_key_and_its_own_known_hosts() -> None:
    command = _host().get_command(["systemctl", "restart", "mariadb"])

    assert command[:5] == ["ssh", "-i", "/home/pilot/.ssh/database", "-l", "ubuntu"]
    assert "BatchMode=yes" in command
    assert "UserKnownHostsFile=/home/pilot/.ssh/database.known_hosts" in command
    assert command[-3] == "fdaa:1:2::3"
    assert _remote_argv(command) == ["sudo", "-n", "systemctl", "restart", "mariadb"]


def test_command_without_sudo_runs_as_the_login_user() -> None:
    assert _remote_argv(_host().get_command(["true"], sudo=False)) == ["true"]


def test_write_sends_the_text_on_stdin_and_replaces_the_file_in_one_step() -> None:
    with patch(f"{MODULE}.run_command", return_value=_completed()) as run:
        _host().write_text("/etc/mysql/mariadb.conf.d/90-pilot.cnf", "[mysqld]\n")

    command = run.call_args.args[0]
    remote = _remote_argv(command)
    assert remote[:4] == ["sudo", "-n", "sh", "-c"]
    assert 'mv "$1.pilot-tmp" "$1"' in remote[4]
    assert remote[5:] == ["sh", "/etc/mysql/mariadb.conf.d/90-pilot.cnf", "0644"]
    assert run.call_args.kwargs["stdin_text"] == "[mysqld]\n"


def test_total_memory_is_read_from_meminfo() -> None:
    meminfo = "MemTotal:        4009276 kB\nMemFree:          144000 kB\n"
    with patch(f"{MODULE}.run_command", return_value=_completed(meminfo)):
        assert _host().total_memory_mb == 3915


def test_free_disk_is_the_last_df_value() -> None:
    with patch(f"{MODULE}.run_command", return_value=_completed("    Avail\n39728541696\n")):
        assert _host().get_free_disk_bytes("/var/lib/mysql") == 39728541696


def test_client_address_is_the_first_ssh_client_field() -> None:
    with patch(f"{MODULE}.run_command", return_value=_completed("fdaa:1:2::7 51234 22\n")) as run:
        assert _host().get_client_address() == "fdaa:1:2::7"
    assert _remote_argv(run.call_args.args[0])[0] == "sh"


def test_missing_client_address_fails_loudly() -> None:
    with patch(f"{MODULE}.run_command", return_value=_completed("\n")), pytest.raises(CommandError):
        _host().get_client_address()


def test_unreachable_host_is_reported_without_raising() -> None:
    with patch(f"{MODULE}.run_command", side_effect=CommandError("refused", returncode=255)):
        assert _host().is_reachable() is False


@pytest.mark.parametrize(("output", "expected"), [("yes\n", True), ("no\n", False)])
def test_exists_reads_the_remote_test(output: str, expected: bool) -> None:
    with patch(f"{MODULE}.run_command", return_value=_completed(output)):
        assert _host().exists("/etc/mysql/mariadb.conf.d/90-pilot.cnf") is expected
