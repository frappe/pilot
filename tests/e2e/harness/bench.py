"""Owns the full lifecycle of one bench for an e2e run.

    new            create()        -> writes benches/<name>/bench.toml
    start (wizard) start_wizard()  -> foreground admin in --wizard mode
    <browser drives the setup wizard; the wizard server SIGTERMs itself>
    start (bench)  start_full()    -> the initialized bench + admin
    stop/destroy   stop() / destroy()

Each start spawns ``bench ... start`` in its own session (process group) so
stop() can tear down the whole tree (admin, workers, redis, gunicorn).
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import time
import tomllib
from pathlib import Path
from shutil import rmtree
from urllib.request import urlopen

# tests/e2e/harness/bench.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHES_DIR = REPO_ROOT / "benches"

# The CLI entry point. CI installs the `bench` console script (pip install -e),
# but the in-repo launcher works everywhere (stdlib-only, python3 shebang), so
# default to it and let BENCH_BIN override.
BENCH_BIN = os.environ.get("BENCH_BIN") or str(REPO_ROOT / "bench")

# Only ever destroy benches we created, so a misconfigured run can't delete a
# developer's real bench.
E2E_PREFIX = "e2e-"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class Bench:
    def __init__(
        self,
        name: str,
        env: dict[str, str] | None = None,
        admin_password: str | None = None,
    ) -> None:
        if not name.startswith(E2E_PREFIX):
            raise ValueError(f'Bench name must start with "{E2E_PREFIX}" (got "{name}")')
        self.name = name
        self._extra_env = env or {}
        # `bench new` no longer pre-generates an admin password (it's set in the
        # wizard's first step and persisted by PUT /api/v1/setup/configuration).
        # So the harness chooses it, the wizard enters it, and login reuses it.
        # A bare token_urlsafe() isn't guaranteed to satisfy the wizard's password
        # policy (upper + lower + digit + symbol) — fixed affixes guarantee it.
        self._admin_password = admin_password or f"Aa1!{secrets.token_urlsafe(12)}"
        self._proc: subprocess.Popen | None = None
        self._info: dict | None = None

    # ── identity ───────────────────────────────────────────────────────────────

    @property
    def dir(self) -> Path:
        return BENCHES_DIR / self.name

    @property
    def admin_port(self) -> int:
        if not self._info:
            raise RuntimeError("Bench not created yet — call create() first")
        return self._info["admin_port"]

    @property
    def admin_password(self) -> str:
        """The admin password the harness will set via the wizard and log in with
        (the fresh bench.toml has none until the wizard saves it)."""
        return self._admin_password

    @property
    def admin_url(self) -> str:
        return f"http://127.0.0.1:{self.admin_port}"

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def create(self) -> None:
        """`bench new <name>` then read the generated admin port."""
        if self.dir.exists():
            raise RuntimeError(f'Bench "{self.name}" already exists at {self.dir} — clean it up first')
        self._run(["new", self.name])
        if not (self.dir / "bench.toml").exists():
            # `bench` resolves its benches dir as <dir containing pilot>/benches.
            # The repo launcher (<repo>/bench, the default) puts it at <repo>/benches,
            # which is what this harness reads. A `bench` from a non-editable
            # `pip install` lives in site-packages and writes benches there instead,
            # so `bench new` "succeeds" yet nothing appears where we look.
            raise RuntimeError(
                f"`bench new {self.name}` reported success but {self.dir / 'bench.toml'} "
                f"was not created.\nBENCH_BIN={BENCH_BIN} writes benches under a different "
                f"root than this harness reads ({BENCHES_DIR}).\nUse the repo launcher "
                f"(unset BENCH_BIN, or point it at {REPO_ROOT / 'bench'}); an installed "
                f"`bench` from a non-editable pip install writes next to its site-packages."
            )
        self._info = self._read_config()

    def start_wizard(self) -> None:
        """`bench -b <name> start` on an un-initialized bench boots the standalone
        setup-wizard server. Returns once it answers on the admin port."""
        # No-op unless E2E_BUILD_ADMIN is set; otherwise `bench start` downloads
        # the prebuilt wizard UI itself.
        self._build_admin_ui()
        self._spawn_start()
        self._wait_for_admin(expect_wizard=True)

    def wait_for_wizard_exit(self, timeout: float = 5 * 60) -> None:
        """After the wizard finishes it shuts its own server down, which makes the
        foreground ``bench start`` exit. Wait for that so the next start gets a
        clean port."""
        deadline = time.time() + timeout
        start = time.time()
        next_log = start + 15
        while self._proc and self._proc.poll() is None:
            now = time.time()
            if now > deadline:
                raise TimeoutError("Wizard server did not exit in time")
            if now >= next_log:
                print(f"[harness] waiting for wizard server to exit, {int(now - start)}s elapsed", flush=True)
                next_log = now + 15
            time.sleep(1)
        self._proc = None

    def start_full(self) -> None:
        """`bench -b <name> start` on the initialized bench: full admin + workload."""
        # `bench init` (run by the wizard) re-downloads the prebuilt admin dist,
        # clobbering any local build. Rebuild from source only when E2E_BUILD_ADMIN
        # is set (no-op otherwise) — e.g. to exercise *this* branch's admin UI
        # rather than the released bundle.
        self._build_admin_ui()
        self._spawn_start()
        self._wait_for_admin(expect_wizard=False)

    def stop(self) -> None:
        """Best-effort: stop the bench and kill the spawned process tree."""
        subprocess.run(
            [BENCH_BIN, "-b", self.name, "stop"],
            cwd=REPO_ROOT,
            env=self._process_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._kill_child()

    def destroy(self) -> None:
        """Fully tear the bench down and leave no trace.

        Prefer `bench drop`: it removes production services and the dedicated
        MariaDB instance, then the bench dir. It refuses when sites remain (e.g.
        a run that failed mid-lifecycle, or pre-clean of a leftover bench), so
        fall back to manual cleanup whenever the dir survives.
        """
        self.stop()
        self._drop_bench()
        if self.dir.exists():
            self._teardown_dedicated_instance()
            self.remove_dir()

    def _drop_bench(self) -> None:
        """`bench -b <name> drop --yes` — best-effort. No-op if the bench has no
        config yet (nothing to drop)."""
        if not (self.dir / "bench.toml").exists():
            return
        subprocess.run(
            [BENCH_BIN, "-b", self.name, "drop", "--yes"],
            cwd=REPO_ROOT,
            env=self._process_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def remove_dir(self) -> None:
        """Remove the bench directory. Guarded by the e2e- prefix.

        A dedicated bench's dir may hold bind mounts (and root-owned files), so a
        plain rmtree can fail with "device busy" and leave the folder behind.
        Unmount anything mounted under it first, then remove the dir (with
        privileges if needed)."""
        if not (self.name.startswith(E2E_PREFIX) and self.dir.exists()):
            return
        # Guard every sudo/rm below: only ever an e2e- bench right under
        # benches/, never a nested or traversed path.
        if self.dir.parent != BENCHES_DIR:
            rmtree(self.dir, ignore_errors=True)
            return
        self._teardown_mounts_under(self.dir)
        rmtree(self.dir, ignore_errors=True)
        if self.dir.exists():
            subprocess.run(
                ["sudo", "rm", "-rf", str(self.dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _teardown_mounts_under(self, root: Path) -> None:
        """Unmount every mountpoint at/under ``root`` (deepest first), so a
        dropped/failed bench leaves no busy mountpoint. Best-effort."""
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        for _, mountpoint, _ in self._mounts_under(root):
            subprocess.run(["sudo", "umount", "-l", mountpoint], **quiet)

    @staticmethod
    def _mounts_under(root: Path) -> list[tuple[str, str, str]]:
        """(source, mountpoint, fstype) for every mount at/under ``root``,
        deepest mountpoint first. Reads /proc/mounts; best-effort."""
        try:
            lines = Path("/proc/mounts").read_text().splitlines()
        except OSError:
            return []
        root_str = str(root)
        found = [
            (parts[0], parts[1], parts[2])
            for line in lines
            if len(parts := line.split()) >= 3
            and (parts[1] == root_str or parts[1].startswith(root_str + "/"))
        ]
        return sorted(found, key=lambda entry: len(entry[1]), reverse=True)

    # ── diagnostics ────────────────────────────────────────────────────────────

    def setup_task_error(self, max_lines: int = 40) -> str | None:
        """Tail of the most recently failed task's output.log (e.g. the
        wizard-setup task), for surfacing *why* a wizard run failed. Returns None
        when there is no failed task or no readable log."""
        tasks_dir = self.dir / "tasks"
        if not tasks_dir.exists():
            return None

        newest: tuple[Path, float] | None = None
        for task_dir in tasks_dir.iterdir():
            status_file = task_dir / "status"
            if not status_file.exists():
                continue
            try:
                if status_file.read_text().strip() != "failed":
                    continue
            except OSError:
                continue
            mtime = task_dir.stat().st_mtime
            if newest is None or mtime > newest[1]:
                newest = (task_dir, mtime)
        if newest is None:
            return None

        log_path = newest[0] / "output.log"
        if not log_path.exists():
            return None
        try:
            tail = "\n".join(log_path.read_text().splitlines()[-max_lines:]).strip()
            return tail or None
        except OSError:
            return None

    # ── internals ──────────────────────────────────────────────────────────────

    def _build_admin_ui(self) -> None:
        """Build the admin UI from source into admin/backend/static/dist so the
        server serves *this branch's* code instead of the prebuilt bundle.

        Opt-in via E2E_BUILD_ADMIN: off by default, in which case the harness
        never builds and `bench start` serves the prebuilt bundle it downloads
        (the wizard) / already has from bench init (the full bench). Turn it on
        to exercise local frontend changes end to end. Installs frontend deps
        once if missing."""
        if not _env_truthy("E2E_BUILD_ADMIN"):
            return
        print("[e2e] Building admin UI from source (E2E_BUILD_ADMIN)...")
        frontend = REPO_ROOT / "admin" / "frontend"
        if not (frontend / "node_modules").exists():
            if subprocess.run(["npm", "install"], cwd=frontend).returncode != 0:
                raise RuntimeError("admin frontend `npm install` failed")
        if subprocess.run(["npm", "run", "build"], cwd=frontend).returncode != 0:
            raise RuntimeError("admin frontend `npm run build` failed")

    def _teardown_dedicated_instance(self) -> None:
        """Best-effort: a dedicated-DB bench provisions its own MariaDB instance
        (systemd unit + datadir) which lives outside the bench dir, so
        remove_dir() alone would orphan it. Read the instance from bench.toml and
        tear it down. No-op for shared-DB benches (no instance configured)."""
        toml = self.dir / "bench.toml"
        if not toml.exists():
            return
        with open(toml, "rb") as f:
            mariadb = tomllib.load(f).get("mariadb", {})
        instance = mariadb.get("instance", "")
        data_dir = mariadb.get("data_dir", "")
        if not instance:
            return

        def sudo(args: list[str]) -> None:
            subprocess.run(["sudo", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        sudo(["systemctl", "stop", f"mariadb@{instance}"])
        sudo(["systemctl", "disable", f"mariadb@{instance}"])
        sudo(["systemctl", "reset-failed", f"mariadb@{instance}"])
        # Remove the per-instance option group and systemd override. Leaving these
        # behind makes a later bench of the same name reuse stale config (notably a
        # stale port), so clean them up to truly leave no trace.
        sudo(["rm", "-f", f"/etc/mysql/mariadb.conf.d/99-bench-{instance}.cnf"])
        sudo(["rm", "-rf", f"/etc/systemd/system/mariadb@{instance}.service.d"])
        sudo(["systemctl", "daemon-reload"])
        if data_dir and data_dir.startswith("/var/lib/mysql-"):
            sudo(["rm", "-rf", data_dir])

    def _process_env(self) -> dict[str, str]:
        return {**os.environ, **self._extra_env}

    def _run(self, args: list[str]) -> None:
        BENCHES_DIR.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            [BENCH_BIN, *args],
            cwd=REPO_ROOT,
            env=self._process_env(),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"bench {' '.join(args)} failed (exit {res.returncode}):\n{res.stdout}\n{res.stderr}"
            )

    def _spawn_start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("A bench process is already running for this harness")
        # start_new_session=True -> own process group so stop() can signal the
        # whole tree. stdout/stderr inherit so logs stream to the console.
        self._proc = subprocess.Popen(
            [BENCH_BIN, "-b", self.name, "start"],
            cwd=REPO_ROOT,
            env=self._process_env(),
            start_new_session=True,
        )

    def _kill_child(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._proc = None
            return
        try:
            # Negative pgid targets the whole process group.
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass  # already gone
        self._proc = None

    def _wait_for_admin(self, expect_wizard: bool, timeout: float = 5 * 60) -> None:
        """Poll bootstrap until the admin answers in the expected mode."""
        deadline = time.time() + timeout
        start = time.time()
        last = "no response yet"
        next_log = start + 15
        while time.time() < deadline:
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(
                    f"bench start exited early (code {self._proc.returncode}) before the admin came up"
                )
            try:
                with urlopen(f"{self.admin_url}/api/v1/bootstrap", timeout=5) as res:
                    data = json.load(res)
                mode = data.get("mode")
                last = f"mode={mode!r}"
                if (mode == "setup") == expect_wizard:
                    return
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            now = time.time()
            if now >= next_log:
                print(
                    f"[harness] waiting for admin (expect_wizard={expect_wizard}), "
                    f"{int(now - start)}s elapsed, last: {last}",
                    flush=True,
                )
                next_log = now + 15
            time.sleep(1)
        raise TimeoutError(
            f'Admin server for "{self.name}" not ready (expect_wizard={expect_wizard}) '
            f"within {timeout}s; last bootstrap: {last}"
        )

    def _read_config(self) -> dict:
        """Read admin.port from the generated bench.toml. (admin.password is empty
        on a fresh bench — the wizard sets it; see admin_password.)"""
        with open(self.dir / "bench.toml", "rb") as f:
            data = tomllib.load(f)
        return {"admin_port": int(data["admin"]["port"])}
