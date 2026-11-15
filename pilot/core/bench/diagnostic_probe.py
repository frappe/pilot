from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ProbeFailure(Exception):
    pass


def database(settings: dict) -> None:
    engine = settings["db_type"]
    if engine == "sqlite":
        sqlite_database(settings["path"])
        return
    from pilot.core.database import MariaDB, PostgreSQL

    arguments = {key: settings[key] for key in ("host", "port", "user", "password", "database")}
    arguments["connect_timeout"] = 1
    if engine == "mariadb":
        import pymysql  # noqa: F401

        client = MariaDB(**arguments, socket=settings.get("socket"))
    elif engine == "postgres":
        import psycopg2  # noqa: F401

        client = PostgreSQL(**arguments)
    else:
        raise ValueError
    try:
        result = client.execute("SELECT 1", read_only=True)
    except Exception as error:
        cause = error.__cause__ or error
        code = cause.args[0] if cause.args else None
        if code in (1044, 1045, 1698) or getattr(cause, "pgcode", None) in ("28P01", "28000"):
            raise ProbeFailure("authentication_failed") from None
        if isinstance(cause, OSError) or code in (2002, 2003, 2006, 2013):
            raise ProbeFailure("connection_failed") from None
        raise ProbeFailure("database_query_failed") from None
    if result.rows != [[1]]:
        raise ProbeFailure("invalid_response")


def sqlite_database(filename: str) -> None:
    path = Path(filename)
    if not path.is_file():
        raise FileNotFoundError
    connection = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        connection.close()


def redis(settings: dict) -> None:
    endpoint = urlsplit(settings["url"])
    if endpoint.scheme not in ("redis", "rediss") or not endpoint.hostname:
        raise ValueError
    connection = socket.create_connection((endpoint.hostname, endpoint.port or 6379), timeout=1)
    try:
        if endpoint.scheme == "rediss":
            import ssl

            connection = ssl.create_default_context().wrap_socket(
                connection, server_hostname=endpoint.hostname
            )
        with connection.makefile("rb") as stream:
            if endpoint.password is not None:
                credentials = [unquote(endpoint.password)]
                if endpoint.username:
                    credentials.insert(0, unquote(endpoint.username))
                send_redis(connection, ["AUTH", *credentials])
                if stream.readline(512) != b"+OK\r\n":
                    raise ProbeFailure("authentication_failed")
            send_redis(connection, ["PING"])
            response = stream.readline(512)
            if response.startswith((b"-NOAUTH", b"-WRONGPASS")):
                raise ProbeFailure("authentication_failed")
            if response != b"+PONG\r\n":
                raise ProbeFailure("invalid_response")
    finally:
        connection.close()


def send_redis(connection: socket.socket, arguments: list[str]) -> None:
    encoded = [argument.encode("utf-8") for argument in arguments]
    payload = f"*{len(encoded)}\r\n".encode()
    payload += b"".join(f"${len(value)}\r\n".encode() + value + b"\r\n" for value in encoded)
    connection.sendall(payload)


def web(settings: dict) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", settings["port"], timeout=1)
    try:
        connection.request("GET", "/api/method/ping", headers={"Host": settings["site"]})
        response = connection.getresponse()
        try:
            body = json.loads(response.read(512))
        except ValueError:
            raise ProbeFailure("invalid_response") from None
        if response.status != 200 or not isinstance(body, dict) or body.get("message") != "pong":
            raise ProbeFailure("invalid_response")
    finally:
        connection.close()


def failure_result(error: Exception) -> dict:
    if isinstance(error, ProbeFailure):
        return {"status": "error", "reason": str(error)}
    reasons = (
        (ImportError, "unavailable", "dependency_missing"),
        (FileNotFoundError, "error", "database_missing"),
        (TimeoutError, "error", "timeout"),
        (sqlite3.DatabaseError, "error", "database_invalid"),
        (OSError, "error", "connection_failed"),
        ((KeyError, TypeError, ValueError), "error", "configuration_invalid"),
        (http.client.HTTPException, "error", "invalid_response"),
    )
    for exception_type, status, reason in reasons:
        if isinstance(error, exception_type):
            return {"status": status, "reason": reason}
    return {"status": "error", "reason": "probe_failed"}


def processes(settings: dict) -> list[dict]:
    from pilot.core.bench.diagnostic_processes import check_processes

    return check_processes(settings)


def main() -> None:
    try:
        request = json.load(sys.stdin)
        findings = {"database": database, "redis": redis, "web": web, "processes": processes}[
            request["kind"]
        ](request["settings"])
        result = {"status": "ok", "reason": ""}
        if findings is not None:
            result["checks"] = findings
    except Exception as error:
        result = failure_result(error)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
