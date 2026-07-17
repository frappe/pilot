from __future__ import annotations


import os
from pathlib import Path

from admin.backend.app import configure_idle_watchdog, create_app
from pilot.tasks.manager.worker_registry import task_workers

"""WSGI entrypoint for running the admin under gunicorn.

The bench root is passed via the BENCH_ADMIN_ROOT environment variable (set by
the systemd service unit) since gunicorn imports a module-level callable rather
than invoking a main() with argv.
"""

bench_root = Path(os.environ["BENCH_ADMIN_ROOT"])
application = create_app(bench_root)
configure_idle_watchdog(application, bench_root)
task_workers.start(bench_root)
task_workers.install_signal_handlers()
