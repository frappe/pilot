import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from botocore.awsrequest import AWSHTTPSConnection, AWSHTTPSConnectionPool
from botocore.httpsession import URLLib3Session
from urllib3 import PoolManager
from urllib3.util.connection import create_connection

if TYPE_CHECKING:
    from botocore.client import BaseClient

PRIVATE_CONNECT_TIMEOUT_SECONDS = 3


class PrivateHTTPSConnection(AWSHTTPSConnection):
    """Dials the private address of `internal-<host>`, or the public address when that fails."""

    def _new_conn(self) -> socket.socket:
        address = find_private_address(self.host, self.port)
        if address is None:
            return super()._new_conn()

        try:
            sock = create_connection(
                (address, self.port),
                PRIVATE_CONNECT_TIMEOUT_SECONDS,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except OSError:
            return super()._new_conn()

        sock.settimeout(self.timeout)
        return sock


class PrivateHTTPSConnectionPool(AWSHTTPSConnectionPool):
    ConnectionCls = PrivateHTTPSConnection


def route_to_private_address(client: "BaseClient", endpoint_url: str) -> None:
    """Dial the private address of `internal-<host>` but keep `<host>` for TLS, signing and presigned URLs."""
    endpoint = urlsplit(endpoint_url)
    if endpoint.scheme != "https" or not endpoint.hostname:
        return

    port = endpoint.port or 443
    address = find_private_address(endpoint.hostname, port)
    if address is None:
        return

    # Off the private network, keep the public path instead of waiting on every connection.
    try:
        create_connection((address, port), PRIVATE_CONNECT_TIMEOUT_SECONDS).close()
    except OSError:
        return

    manager = get_pool_manager(client)
    manager.pool_classes_by_scheme = {**manager.pool_classes_by_scheme, "https": PrivateHTTPSConnectionPool}


def get_pool_manager(client: "BaseClient") -> PoolManager:
    """botocore has no connect-address setting, so reach its urllib3 manager and fail if that changes."""
    session = client._endpoint.http_session
    manager = getattr(session, "_manager", None)
    if (
        not isinstance(session, URLLib3Session)
        or not isinstance(manager, PoolManager)
        or manager.pool_classes_by_scheme.get("https") is not AWSHTTPSConnectionPool
    ):
        raise TypeError("botocore no longer uses a urllib3 PoolManager with AWSHTTPSConnectionPool")
    return manager


def find_private_address(host: str, port: int) -> str | None:
    """Return the private answer. Public answers come from the Atlas wildcard record."""
    try:
        answers = socket.getaddrinfo(f"internal-{host}", port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None

    for *_, socket_address in answers:
        address = str(socket_address[0])
        if ipaddress.ip_address(address).is_private:
            return address
    return None
