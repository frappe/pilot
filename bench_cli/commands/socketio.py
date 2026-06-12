from __future__ import annotations

import os
import shutil
import subprocess

from bench_cli.core.bench import Bench
from bench_cli.exceptions import BenchError


class SocketIOCommand:
    """Launch the realtime (socketio) server, picking the backend at runtime.

    Backend is read from ``socketio_backend`` in bench.toml ("node" default, or
    "python"). The process replaces itself via ``os.execv`` so the process
    manager (foreground runner / systemd / supervisor) keeps tracking the real
    server pid. If the python backend is requested but the frappe env does not
    ship ``frappe.realtime.server``, it falls back to the node backend.
    """

    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def run(self) -> None:
        backend = getattr(self.bench.config, "socketio_backend", "node")
        if backend not in ("node", "python"):
            print(f"Unknown socketio_backend {backend!r}; falling back to 'node'.")
            backend = "node"

        # process commands run from the sites dir (see _web_definition etc.)
        os.chdir(self.bench.sites_path)

        if backend == "python":
            python = str(self.bench.env_path / "bin" / "python")
            if self._python_backend_supported(python):
                os.execv(python, [python, "-m", "frappe.realtime.server"])
            print(
                "socketio_backend is 'python' but this frappe env has no "
                "'frappe.realtime.server'; falling back to the node backend."
            )

        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            raise BenchError(
                "Cannot start socketio: node not found and the python backend is "
                "unavailable. Install node or a frappe version with "
                "frappe.realtime.server."
            )
        socketio_js = str(self.bench.apps_path / "frappe" / "socketio.js")
        os.execv(node, [node, socketio_js])

    @staticmethod
    def _python_backend_supported(python: str) -> bool:
        """Check the frappe env ships the python realtime server.

        Probes with the frappe env interpreter and find_spec so the module's
        heavy import side-effects (gevent monkey-patching) don't run.
        """
        if not os.path.exists(python):
            return False
        try:
            return (
                subprocess.run(
                    [
                        python,
                        "-c",
                        "import importlib.util, sys;"
                        "sys.exit(0 if importlib.util.find_spec('frappe.realtime.server') else 1)",
                    ],
                    timeout=30,
                ).returncode
                == 0
            )
        except (subprocess.SubprocessError, OSError):
            return False
