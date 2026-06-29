from __future__ import annotations

import argparse
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

_last_request = time.monotonic()


def main() -> None:
    parser = argparse.ArgumentParser(description="bench admin server daemon")
    parser.add_argument("--bench-root", required=True)
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--timeout", type=int, default=900, help="Inactivity timeout in seconds")
    parser.add_argument("--no-timeout", action="store_true", help="Disable inactivity watchdog (used when managed by procfile)")
    parser.add_argument("--dev", action="store_true", help="Enable auto-reload on code changes (development only)")
    parser.add_argument("--wizard", action="store_true", help="Running as the standalone setup-wizard server")
    args = parser.parse_args()

    from admin.backend.app import create_app
    from pilot.config.toml_store import BenchTomlStore

    bench_root = Path(args.bench_root)
    app = create_app(bench_root)
    app.config["WIZARD_SERVER"] = args.wizard

    skip_watchdog = args.no_timeout or args.dev
    if not skip_watchdog:
        try:
            cfg = BenchTomlStore.for_bench(bench_root).read()
            skip_watchdog = cfg.admin.enabled
        except Exception:
            pass

    if not skip_watchdog:

        @app.before_request
        def _touch() -> None:
            global _last_request
            _last_request = time.monotonic()

        def _watchdog() -> None:
            while True:
                time.sleep(60)
                if time.monotonic() - _last_request > args.timeout:
                    os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=_watchdog, daemon=True).start()

    # Start vite only in the outer watcher process, not in the child that
    # werkzeug's reloader spawns (WERKZEUG_RUN_MAIN is set in that child).
    if args.dev and not os.environ.get("WERKZEUG_RUN_MAIN"):
        _start_vite_watch()

    # "::" makes Werkzeug bind a dual-stack socket, so the admin is reachable
    # over both IPv4 and IPv6 in dev (where there is no nginx in front).
    app.run(host="::", port=args.port, threaded=True, use_reloader=args.dev)


def _start_vite_watch() -> None:
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if not frontend_dir.exists():
        return

    def _run() -> None:
        subprocess.run(["node_modules/.bin/vite", "build", "--watch", "--mode", "development"], cwd=str(frontend_dir))

    threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    main()
