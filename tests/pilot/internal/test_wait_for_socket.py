from __future__ import annotations

import socket
from pathlib import Path

from pilot.internal import wait_for_socket


def test_a_listening_unix_socket_is_accepting(tmp_path: Path) -> None:
    path = tmp_path / "db.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen()

    assert wait_for_socket.main(f"unix:{path}", timeout_seconds=1) == 0
    listener.close()


def test_a_listening_tcp_port_is_accepting() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    assert wait_for_socket.main(f"tcp:127.0.0.1:{listener.getsockname()[1]}", timeout_seconds=1) == 0
    listener.close()


def test_a_stale_socket_file_times_out(tmp_path: Path, capsys) -> None:
    path = tmp_path / "db.sock"
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(path))
    stale.close()

    assert wait_for_socket.main(f"unix:{path}", timeout_seconds=0) == 1
    assert "did not become ready" in capsys.readouterr().err
