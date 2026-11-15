import json
import os
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import venv
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def protocol_server(response: bytes):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            first = self.rfile.readline()
            if first.startswith(b"GET "):
                while self.rfile.readline() not in (b"\r\n", b""):
                    pass
            else:
                self.rfile.readline()
                self.rfile.readline()
            self.wfile.write(response)
            self.wfile.flush()

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=1)


def main() -> None:
    """Demonstrate doctor against real disposable files, with no Frappe installation."""
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository))
    from pilot.config import BenchConfig

    directory = Path(tempfile.mkdtemp(prefix="pilot-doctor-demo-"))
    bench = directory / "bench"
    bench.mkdir()
    BenchConfig.from_flat("demo", {"db_type": "sqlite"}).write(bench)
    venv.EnvBuilder(with_pip=False, symlinks=True).create(bench / "env")
    app = bench / "apps" / "frappe"
    (app / "frappe").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(app)], check=True)
    (app / "frappe" / "hooks.py").write_text("raise RuntimeError('Do not import app code')\n")
    site = bench / "sites" / "demo.localhost"
    site.mkdir(parents=True)
    (site.parent / "apps.txt").write_text("frappe\n")
    (site.parent / "common_site_config.json").write_text('{"db_type": "sqlite"}')
    config = site / "site_config.json"
    config.write_text('{"db_name": "demo"}')
    environment = dict(os.environ, PYTHONPATH=str(repository))
    command = [sys.executable, "-c", "from pilot.cli import main; main()", "doctor", "--path", str(bench)]

    print(f"Demo directory (kept for inspection): {directory}", flush=True)
    print("This is a local file fixture, not an installed or running Frappe bench.", flush=True)
    for stage, expected in (("clean", 0), ("broken site JSON", 1), ("repaired", 0)):
        config.write_text("{" if expected else '{"db_name": "demo"}')
        print(f"\n--- {stage} ---", flush=True)
        result = subprocess.run([*command, "--offline"], env=environment, check=False)
        print(f"Exit status: {result.returncode}", flush=True)
        if result.returncode != expected:
            raise SystemExit("Unexpected doctor result")
    database = site / "db" / "demo.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example (value TEXT)")
    original = database.read_bytes()
    body = b'{"message":"pong"}'
    response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    with (
        protocol_server(b"+PONG\r\n") as cache,
        protocol_server(b"+PONG\r\n") as queue,
        protocol_server(response) as http,
    ):
        settings = BenchConfig.read(bench)
        settings.redis.cache_port, settings.redis.queue_port, settings.http_port = cache, queue, http
        settings.write(bench)
        for label, expected in (
            ("runtime healthy", 0),
            ("runtime database corrupt", 1),
            ("runtime repaired", 0),
        ):
            database.write_bytes(b"corrupt database" if expected else original)
            result = subprocess.run(
                [*command, "--json"], env=environment, capture_output=True, text=True, check=False
            )
            report = json.loads(result.stdout)
            print(
                f"\n{label}: exit={result.returncode}, complete={report['complete']}, summary={report['summary']}",
                flush=True,
            )
            if result.returncode != expected:
                raise SystemExit("Unexpected runtime doctor result")
    result = subprocess.run(
        [*command, "--offline", "--json"], env=environment, capture_output=True, text=True, check=True
    )
    report = json.loads(result.stdout)
    print(f"\nJSON parsed: schema_version={report['schema_version']}, ok={report['ok']}", flush=True)
    print(f"Inspect the files: pilot doctor --offline --path {bench}", flush=True)
    print(
        "Loopback protocol fixtures have stopped; default doctor will now report unavailable services.",
        flush=True,
    )


if __name__ == "__main__":
    main()
