from __future__ import annotations

import socket

import pytest

from pilot.internal import wait_for_socket


def test_a_listening_port_is_accepting() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    assert wait_for_socket.main(f"127.0.0.1:{listener.getsockname()[1]}", timeout_seconds=1) == 0
    listener.close()


def test_a_closed_port_times_out_with_a_message(capsys: pytest.CaptureFixture) -> None:
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()

    assert wait_for_socket.main(f"127.0.0.1:{port}", timeout_seconds=0) == 1
    assert "did not become ready" in capsys.readouterr().err


@pytest.mark.parametrize("address", ["localhost", "localhost:", "localhost:port", "unix:/run/db.sock"])
def test_a_malformed_address_is_a_clear_error(address: str, capsys: pytest.CaptureFixture) -> None:
    assert wait_for_socket.main(address, timeout_seconds=0) == 2
    assert "HOST:PORT" in capsys.readouterr().err
