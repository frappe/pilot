import json
import os
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from pilot.core.bench.diagnostic_runtime import RuntimeDiagnostics
from pilot.core.bench.diagnostics import DiagnosticReport


@contextmanager
def protocol_server(respond):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            respond(self.rfile, self.wfile)

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=1)


def runtime(tmp_path: Path, timeout: float = 2) -> RuntimeDiagnostics:
    return RuntimeDiagnostics(DiagnosticReport(str(tmp_path)), offline=False, timeout=timeout)


@pytest.mark.parametrize("state", ["healthy", "missing", "corrupt"])
def test_sqlite_probe_reads_existing_database_without_creating_or_changing_files(tmp_path, state):
    path = tmp_path / "site.db"
    if state == "healthy":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE example (value TEXT)")
    elif state == "corrupt":
        path.write_bytes(b"this is not a SQLite database")
    before = {file.name: file.read_bytes() for file in tmp_path.iterdir()}

    result = runtime(tmp_path).execute("database", {"db_type": "sqlite", "path": str(path)})

    assert result["status"] == ("ok" if state == "healthy" else "error")
    if state != "healthy":
        assert result["reason"] == ("database_missing" if state == "missing" else "database_invalid")
    assert {file.name: file.read_bytes() for file in tmp_path.iterdir()} == before


def read_redis_command(stream):
    count = int(stream.readline()[1:])
    arguments = []
    for _ in range(count):
        size = int(stream.readline()[1:])
        arguments.append(stream.read(size))
        stream.read(2)
    return arguments


@pytest.mark.parametrize("authenticated", [True, False])
def test_redis_probe_authenticates_and_requires_protocol_pong(tmp_path, authenticated):
    commands = []

    def respond(reader, writer):
        commands.append(read_redis_command(reader))
        writer.write(b"+OK\r\n" if authenticated else b"-WRONGPASS invalid password\r\n")
        writer.flush()
        if authenticated:
            commands.append(read_redis_command(reader))
            writer.write(b"+PONG\r\n")
            writer.flush()

    with protocol_server(respond) as port:
        result = runtime(tmp_path).execute("redis", {"url": f"redis://user:secret@127.0.0.1:{port}"})

    assert commands == [[b"AUTH", b"user", b"secret"], *([[b"PING"]] if authenticated else [])]
    assert result == {
        "status": "ok" if authenticated else "error",
        "reason": "" if authenticated else "authentication_failed",
    }
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("body", [b'{"message":"pong"}', b'{"message":"not pong"}'])
def test_http_probe_routes_site_and_validates_frappe_response(tmp_path, body):
    requests = []

    def respond(reader, writer):
        headers = []
        while line := reader.readline():
            headers.append(line)
            if line == b"\r\n":
                break
        requests.append(b"".join(headers))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        writer.flush()

    with protocol_server(respond) as port:
        result = runtime(tmp_path).execute("web", {"port": port, "site": "example.localhost"})

    assert b"GET /api/method/ping HTTP/1.1\r\n" in requests[0]
    assert b"Host: example.localhost\r\n" in requests[0]
    assert result["status"] == ("ok" if body == b'{"message":"pong"}' else "error")
    if result["status"] == "error":
        assert result["reason"] == "invalid_response"


def test_whole_probe_deadline_stops_a_peer_that_accepts_but_never_replies(tmp_path):
    release = threading.Event()

    def respond(reader, writer):
        release.wait(timeout=3)

    with protocol_server(respond) as port:
        started = time.monotonic()
        try:
            result = runtime(tmp_path, timeout=0.3).execute("redis", {"url": f"redis://127.0.0.1:{port}"})
        finally:
            release.set()

    assert result == {"status": "error", "reason": "timeout"}
    assert time.monotonic() - started < 1.5


def test_runtime_child_imports_from_source_checkout_when_cli_starts_in_a_site(tmp_path):
    repository = Path(__file__).resolve().parents[3]
    bench = tmp_path / "bench"
    bench.mkdir()
    from pilot.config import BenchConfig

    BenchConfig.from_flat("demo", {"db_type": "sqlite"}).write(bench)
    site = bench / "sites" / "demo.localhost"
    site.mkdir(parents=True)
    (site.parent / "common_site_config.json").write_text('{"db_type":"sqlite"}')
    (site / "site_config.json").write_text('{"db_name":"demo"}')
    entrypoint = tmp_path / "pilot.py"
    entrypoint.write_text(
        f"import sys\nsys.path.insert(0, {str(repository)!r})\nfrom pilot.cli import main\nmain()\n"
    )
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(entrypoint), "doctor", "--json", "--timeout", "0.5"],
        cwd=site,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = [check for check in report["checks"] if check["code"] == "database_connectivity"]
    assert checks[0]["reason"] == "database_missing"
    assert checks[0]["scope"] == "site" and checks[0]["target"] == "demo.localhost"
    assert report["summary"]["error"] > 0


def test_missing_optional_database_driver_reports_unavailable_without_credentials(tmp_path):
    request = {
        "kind": "database",
        "settings": {
            "db_type": "postgres",
            "host": "localhost",
            "port": 5432,
            "user": "site",
            "password": "never-print-this",
            "database": "site",
        },
    }
    repository = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [sys.executable, "-S", "-m", "pilot.core.bench.diagnostic_probe"],
        input=json.dumps(request),
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(result.stdout) == {"status": "unavailable", "reason": "dependency_missing"}
    assert "never-print-this" not in result.stdout + result.stderr


def test_offline_reports_skipped_probes_without_claiming_complete_runtime_health(tmp_path):
    report = DiagnosticReport(str(tmp_path), mode="offline")
    diagnostic = RuntimeDiagnostics(report, offline=True, timeout=2)

    diagnostic.probe("redis", "service", "redis_cache", {"url": "redis://secret:password@invalid"})

    result = report.to_dict()
    assert result["checks"][0]["status"] == "skipped"
    assert result["checks"][0]["reason"] == "offline"
    assert result["complete"] is False
    assert "password" not in json.dumps(result)
