from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

CONTROL_SOCKET = "handoff.sock"
START_TIMEOUT = 30
DRAIN_TIMEOUT = 35
ACTIVATION_FD = 3


class WebHandoff:
    """Run successive Gunicorn masters on one systemd listener."""

    def __init__(self, argv: list[str], working_directory: Path) -> None:
        self.argv = argv
        self.working_directory = working_directory
        self.is_stopping = False

    def stop(self, *_args) -> None:
        self.is_stopping = True

    def serve(self) -> None:
        if os.environ.pop("LISTEN_PID", "") != str(os.getpid()) or os.environ.pop("LISTEN_FDS", "") != "1":
            raise RuntimeError("Expected one systemd web socket")
        notify_socket = os.environ.pop("NOTIFY_SOCKET")
        if notify_socket.startswith("@"):
            notify_socket = "\0" + notify_socket[1:]
        signal.signal(signal.SIGTERM, self.stop)
        with socket.socket(socket.AF_UNIX) as control:
            control.bind(CONTROL_SOCKET)
            control.listen()
            control.settimeout(0.2)
            master = self.start_master()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                    notify.sendto(b"READY=1", notify_socket)
                while not self.is_stopping:
                    if master.poll() is not None:
                        raise RuntimeError(f"Gunicorn exited with status {master.returncode}")
                    try:
                        connection, _ = control.accept()
                    except TimeoutError:
                        continue
                    master = self.handle_connection(master, connection)
            finally:
                self.drain(master)

    def handle_connection(self, master: subprocess.Popen, connection: socket.socket) -> subprocess.Popen:
        with connection, connection.makefile("rb") as request:
            try:
                connection.settimeout(START_TIMEOUT)
                command = request.readline().decode().rstrip("\n")
                if command == "reload":
                    return self.handoff(master, connection)
                send_reply(connection, "Unknown handoff command")
            except (OSError, RuntimeError) as error:
                with suppress(OSError):
                    send_reply(connection, str(error))
        return master

    def handoff(self, master: subprocess.Popen, connection: socket.socket) -> subprocess.Popen:
        replacement = self.start_master(check_health=True)
        self.drain(master)
        with suppress(OSError):
            send_reply(connection, "ok")
        return replacement

    def start_master(self, *, check_health: bool = False) -> subprocess.Popen:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory, socket.socket(socket.AF_UNIX) as probe:
            address = bind_probe(probe, Path(directory))
            hold = Path(directory) / "hold" if check_health else None
            if hold:
                hold.touch()
                hold.with_name("probe").touch()
            master = self.spawn_master(probe, self.working_directory, hold)
            try:
                if hold:
                    self.wait_until_ready(master, lambda: hold.with_name("healthy").exists())
                    hold.unlink()
                self.wait_for_worker(master, address)
            except BaseException:
                self.drain(master)
                raise
        return master

    def spawn_master(self, probe: socket.socket, sites: Path, hold: Path | None = None) -> subprocess.Popen:
        executable = Path(self.argv[0])
        command = ["master", str(probe.fileno())]
        if hold:
            command += ["--hold", str(hold)]
        return subprocess.Popen(
            [str(executable.with_name("python")), __file__, *command, str(executable), *self.argv[1:]],
            cwd=sites,
            pass_fds=(ACTIVATION_FD, probe.fileno()),
            start_new_session=True,
        )

    def wait_for_worker(self, master: subprocess.Popen, address: str) -> None:
        """Wait for an HTTP response on a listener only this generation can accept."""
        self.wait_until_ready(master, lambda: probe_worker(address))

    def wait_until_ready(self, master: subprocess.Popen, ready) -> None:
        deadline = time.monotonic() + START_TIMEOUT
        while not self.is_stopping and time.monotonic() < deadline:
            if master.poll() is not None:
                raise RuntimeError(f"New Gunicorn exited with status {master.returncode}")
            if ready():
                return
            time.sleep(0.05)
        raise RuntimeError("New Gunicorn did not become ready")

    @staticmethod
    def drain(master: subprocess.Popen) -> None:
        master.terminate()
        try:
            master.wait(timeout=DRAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(master.pid, signal.SIGKILL)
            master.wait()


def bind_probe(probe: socket.socket, directory: Path) -> str:
    address = str(directory.relative_to(Path.cwd()) / "probe.sock")
    probe.bind(address)
    probe.listen()
    return address


def probe_worker(address: str) -> bool:
    with socket.socket(socket.AF_UNIX) as client, suppress(OSError):
        client.settimeout(0.2)
        client.connect(address)
        client.sendall(b"GET /api/method/ping HTTP/1.0\r\nHost: pilot-readiness.invalid\r\n\r\n")
        with client.makefile("rb") as response:
            return response.readline(128).startswith(b"HTTP/")
    return False


def send_reply(connection: socket.socket, result: str) -> None:
    connection.sendall(result.encode() + b"\n")


def check_reply(response) -> None:
    result = response.readline().decode().strip()
    if result != "ok":
        raise RuntimeError(result or "Gunicorn handoff interrupted")


def reload_web() -> None:
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(CONTROL_SOCKET)
        client.sendall(b"reload\n")
        with client.makefile("rb") as response:
            check_reply(response)


def exec_master(probe_fd: int, hold: str, argv: list[str]) -> None:
    """Pass both listeners as activation sockets so Gunicorn leaves their paths alone."""
    if probe_fd != ACTIVATION_FD + 1:
        os.dup2(probe_fd, ACTIVATION_FD + 1)
        os.close(probe_fd)
    os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="2")
    if hold:
        run_prepared_master(Path(hold), argv)
        return
    os.execv(argv[0], argv)


def run_prepared_master(hold: Path, argv: list[str]) -> None:
    from gunicorn.app.wsgiapp import WSGIApplication

    def wait_for_activation(worker) -> None:
        if not hold.exists():
            return
        # A faster worker can complete activation and retire these markers.
        with suppress(FileNotFoundError):
            hold.with_name("ready").touch()
        checked = False
        while hold.exists():
            if not worker.alive:
                raise SystemExit(0)
            worker.notify()
            if not checked and hold.with_name("probe").exists():
                check_sites(worker.wsgi)
                checked = True
                with suppress(FileNotFoundError):
                    hold.with_name("healthy").touch()
            time.sleep(0.02)

    sys.argv = argv
    application = WSGIApplication("%(prog)s [OPTIONS] [APP_MODULE]")
    previous_hook = application.cfg.post_worker_init

    def initialize_worker(worker) -> None:
        previous_hook(worker)
        wait_for_activation(worker)

    application.cfg.set("post_worker_init", initialize_worker)
    application.run()


def check_sites(application) -> None:
    """Exercise Frappe request initialization in a worker before it accepts traffic."""
    import frappe
    from werkzeug.test import Client

    initialize = frappe.init

    def init_for_probe(*args, **kwargs):
        initialize(*args, **kwargs)
        frappe.local.conf = frappe._dict(frappe.local.conf)
        frappe.local.conf.maintenance_mode = 0

    client = Client(application)
    sites = Path.cwd()
    with patch.object(frappe, "init", init_for_probe):
        for config in sites.glob("*/site_config.json"):
            site = config.parent.name
            response = client.get(
                "/api/method/ping", base_url=f"http://{site}", environ_overrides={"pilot.internal_probe": True},
            )
            try:
                if response.status_code != 200 or response.json != {"message": "pong"}:
                    raise RuntimeError(f"Release health check failed for {site}: HTTP {response.status_code}")
            finally:
                response.close()


def maintenance(app, environ, start_response):
    return app.Response(
        "Site is under maintenance.\n", status=503, mimetype="text/plain", headers={GATE_HEADER: "closed"},
    )(
        environ, start_response
    )


def gate_request(app, site, environ, start_response):
    if is_site_gated(site):
        return maintenance(app, environ, start_response)
    if not site or Path(site).name != site or site == "..":
        return app.application(environ, start_response)
    try:
        descriptor = open_site_directory(Path(site))
    except (FileNotFoundError, NotADirectoryError):
        return app.application(environ, start_response)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        if is_site_gated(site):
            return SiteResponse(maintenance(app, environ, start_response), descriptor)
        return SiteResponse(app.application(environ, start_response), descriptor)
    except BaseException:
        os.close(descriptor)
        raise

def main() -> None:
    if sys.argv[1] == "master":
        probe_fd = int(sys.argv[2])
        if sys.argv[3] == "--hold":
            exec_master(probe_fd, sys.argv[4], sys.argv[5:])
        else:
            exec_master(probe_fd, "", sys.argv[3:])
        return
    os.chdir(os.environ["RUNTIME_DIRECTORY"])
    if sys.argv[1] == "reload":
        reload_web()
        return
    WebHandoff(sys.argv[3:], Path(sys.argv[2])).serve()


if __name__ == "__main__":
    main()
