import socket
from unittest.mock import Mock

import boto3
import pytest

from pilot.integrations.s3 import private_network

ENDPOINT = "https://s3-svc.example.test"


def answers(*addresses: str):
    return lambda *_, **__: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0)) for address in addresses
    ]


def build_client():
    return boto3.client(
        "s3",
        aws_access_key_id="access",
        aws_secret_access_key="secret",
        endpoint_url=ENDPOINT,
        region_name="garage",
    )


def https_pool_class(client):
    return client._endpoint.http_session._manager.pool_classes_by_scheme["https"]


def test_private_address_ignores_public_answers(monkeypatch) -> None:
    monkeypatch.setattr(private_network.socket, "getaddrinfo", answers("2606:4700::1111", "fdaa:1::7"))
    assert private_network.find_private_address("s3-svc.example.test", 443) == "fdaa:1::7"

    monkeypatch.setattr(private_network.socket, "getaddrinfo", answers("2606:4700::1111"))
    assert private_network.find_private_address("proxy.example.test", 443) is None


def test_unreachable_private_address_keeps_the_public_path(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(private_network.socket, "getaddrinfo", answers("fdaa:1::7"))
    monkeypatch.setattr(private_network, "create_connection", Mock(side_effect=OSError("unreachable")))

    private_network.route_to_private_address(client, ENDPOINT)

    assert https_pool_class(client) is not private_network.PrivateHTTPSConnectionPool


def test_client_dials_the_private_address_and_keeps_the_host(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(private_network.socket, "getaddrinfo", answers("fdaa:1::7"))
    connect = Mock()
    monkeypatch.setattr(private_network, "create_connection", connect)

    private_network.route_to_private_address(client, ENDPOINT)
    connection = https_pool_class(client).ConnectionCls(host="s3-svc.example.test", port=443, timeout=60)
    connection._new_conn()

    assert connection.host == "s3-svc.example.test"
    assert connect.call_args.args[0] == ("fdaa:1::7", 443)
    connect.return_value.settimeout.assert_called_once_with(60)


def test_changed_botocore_session_fails_loudly() -> None:
    client = Mock()
    client._endpoint.http_session = object()

    with pytest.raises(TypeError, match="urllib3 PoolManager"):
        private_network.get_pool_manager(client)
