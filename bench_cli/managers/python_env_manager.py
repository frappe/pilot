from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.exceptions import BenchError
from bench_cli.platform import is_macos, which
from bench_cli.utils import get_yarn_bin, run_command

if TYPE_CHECKING:
    from bench_cli.core.app import App
    from bench_cli.core.bench import Bench

_BUNDLE_RE = re.compile(r"^(.+)\.bundle\.[A-Z0-9]{8}\.(js|css)$")


class PythonEnvManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def ensure_python(self) -> None:
        pass

    def create_venv(self) -> None:
        if self.bench.python.exists():
            return
        uv = self._ensure_uv()
        version = self.bench.config.python_version
        run_command([uv, "venv", "--python", version, str(self.bench.env_path)], stream_output=True)

    def _build_env(self) -> dict | None:
        """Environment for compiling Python C extensions during install.

        On macOS, mysqlclient's build can't find MariaDB through pkg-config
        (Homebrew doesn't expose a matching `.pc` on the default search path),
        so it aborts with "Can not find valid pkg-config name". We feed it the
        flags from `mariadb_config` directly via MYSQLCLIENT_CFLAGS/LDFLAGS,
        which makes the build skip pkg-config entirely. On Linux the system
        `libmariadb-dev` package provides the pkg-config file, so no override is
        needed.
        """
        if not is_macos():
            return None

        import os
        import subprocess

        config_bin = self._mariadb_config_bin()
        if not config_bin:
            return None

        env = os.environ.copy()
        try:
            env.setdefault(
                "MYSQLCLIENT_CFLAGS",
                subprocess.run([config_bin, "--cflags"], capture_output=True, text=True, check=True).stdout.strip(),
            )
            env.setdefault(
                "MYSQLCLIENT_LDFLAGS",
                subprocess.run([config_bin, "--libs"], capture_output=True, text=True, check=True).stdout.strip(),
            )
        except subprocess.CalledProcessError:
            return None
        return env

    @staticmethod
    def _mariadb_config_bin() -> str | None:
        """Locate mariadb_config (or mysql_config), falling back to the Homebrew
        keg in case the formula is keg-only and not on PATH."""
        import subprocess

        if found := (shutil.which("mariadb_config") or shutil.which("mysql_config")):
            return found
        try:
            prefix = subprocess.run(["brew", "--prefix", "mariadb"], capture_output=True, text=True, check=True).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        candidate = Path(prefix) / "bin" / "mariadb_config"
        return str(candidate) if candidate.exists() else None

    def install_app(self, app: "App") -> None:
        uv = self._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command(
            [uv, "pip", "install", "--python", python, "-e", str(app.path)],
            stream_output=True,
            env=self._build_env(),
        )

    def uninstall_app(self, app_name: str) -> None:
        uv = self._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command([uv, "pip", "uninstall", "--python", python, app_name], stream_output=True)

    def install_node(self) -> None:
        if not which("node"):
            if is_macos():
                run_command(["brew", "install", "node"])
            else:
                self._install_node_linux()
        if not which("yarn"):
            self._install_yarn()

    def _install_yarn(self) -> None:
        if is_macos():
            run_command(["npm", "install", "-g", "yarn"])
        else:
            npm_prefix = Path.home() / ".local"
            npm_prefix.mkdir(parents=True, exist_ok=True)
            run_command(["npm", "install", "-g", "yarn", "--prefix", str(npm_prefix)])

    def install_node_dependencies(self) -> None:
        for app in self.bench.apps():
            if (app.path / "package.json").exists():
                run_command(
                    [get_yarn_bin(), "install", "--frozen-lockfile"],
                    cwd=app.path,
                    stream_output=True,
                )

    def build_assets(self) -> None:
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path,
            stream_output=True,
        )

    def build_assets_for_app(self, app: "App") -> None:
        app_public_dir = app.path / app.config.name / "public"
        dist_dir = app_public_dir / "dist"

        if self._try_download_prebuilt_assets(app, app_public_dir, dist_dir):
            return

        if self._has_prebuilt_assets(dist_dir):
            self._setup_prebuilt_assets(app.config.name, app_public_dir, dist_dir)
            return

        if (app.path / "package.json").exists():
            print(f"  Installing JS dependencies for {app.config.name}...")
            sys.stdout.flush()
            run_command(
                [get_yarn_bin(), "install", "--frozen-lockfile"],
                cwd=app.path,
                stream_output=True,
            )

        print(f"  Building assets for {app.config.name}...")
        sys.stdout.flush()
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force", "--app", app.config.name],
            cwd=self.bench.sites_path,
            stream_output=True,
        )

        for frontend_dir in ["frontend", "roster"]:
            if (app.path / frontend_dir / "package.json").exists():
                print(f"  Building {frontend_dir} for {app.config.name}...")
                sys.stdout.flush()
                run_command(
                    [get_yarn_bin(), "build"],
                    cwd=app.path / frontend_dir,
                    stream_output=True,
                )

    def _try_download_prebuilt_assets(self, app: "App", app_public_dir: Path, dist_dir: Path) -> bool:
        branch = self._app_branch(app)
        if not branch:
            return False
        url = self._release_asset_url(app, branch)
        if not url:
            return False
        print(f"  Downloading pre-built assets for {app.config.name}...")
        sys.stdout.flush()
        if not self._download_and_extract(url, app_public_dir):
            return False
        self._setup_prebuilt_assets(app.config.name, app_public_dir, dist_dir)
        return True

    @staticmethod
    def _app_branch(app: "App") -> str | None:
        import subprocess

        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=app.path,
        )
        branch = r.stdout.strip()
        return branch if r.returncode == 0 and branch not in ("HEAD", "") else None

    @staticmethod
    def _release_asset_url(app: "App", branch: str) -> str | None:
        import subprocess

        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=app.path,
        )
        if r.returncode != 0:
            return None
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", r.stdout.strip())
        if not m:
            return None
        owner_repo = m.group(1)
        tag = f"assets-{branch.replace('/', '-')}"
        return f"https://github.com/{owner_repo}/releases/download/{tag}/{app.config.name}-assets.tar.gz"

    @staticmethod
    def _download_and_extract(url: str, dest_dir: Path) -> bool:
        import tarfile as tf
        import tempfile
        import urllib.error
        import urllib.request

        tmp_path = Path(tempfile.mktemp(suffix=".tar.gz"))
        try:
            urllib.request.urlretrieve(url, tmp_path)
        except urllib.error.URLError:
            tmp_path.unlink(missing_ok=True)
            return False

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with tf.open(tmp_path) as tar:
                tar.extractall(path=dest_dir)
            return True
        except Exception:
            return False
        finally:
            tmp_path.unlink(missing_ok=True)

    def _has_prebuilt_assets(self, dist_dir: Path) -> bool:
        js_dir = dist_dir / "js"
        return js_dir.is_dir() and any(_BUNDLE_RE.match(f.name) for f in js_dir.iterdir())

    def _setup_prebuilt_assets(self, app_name: str, app_public_dir: Path, dist_dir: Path) -> None:
        assets_dir = self.bench.sites_path / "assets"
        assets_dir.mkdir(exist_ok=True)

        app_link = assets_dir / app_name
        if app_link.is_symlink():
            app_link.unlink()
        elif app_link.is_dir():
            shutil.rmtree(str(app_link))
        app_link.symlink_to(app_public_dir.resolve())

        self._write_assets_json(app_name, dist_dir, assets_dir)
        print(f"  Linked {app_link} -> {app_public_dir.resolve()}")

    def _write_assets_json(self, app_name: str, dist_dir: Path, assets_dir: Path) -> None:
        assets: dict[str, str] = {}
        rtl_assets: dict[str, str] = {}

        js_dir = dist_dir / "js"
        if js_dir.is_dir():
            for f in sorted(js_dir.iterdir()):
                m = _BUNDLE_RE.match(f.name)
                if m and m.group(2) == "js":
                    assets[f"{m.group(1)}.bundle.js"] = f"/assets/{app_name}/dist/js/{f.name}"

        css_dir = dist_dir / "css"
        if css_dir.is_dir():
            for f in sorted(css_dir.iterdir()):
                m = _BUNDLE_RE.match(f.name)
                if m and m.group(2) == "css":
                    assets[f"{m.group(1)}.bundle.css"] = f"/assets/{app_name}/dist/css/{f.name}"

        rtl_dir = dist_dir / "css-rtl"
        if rtl_dir.is_dir():
            for f in sorted(rtl_dir.iterdir()):
                m = _BUNDLE_RE.match(f.name)
                if m and m.group(2) == "css":
                    rtl_assets[f"rtl_{m.group(1)}.bundle.css"] = f"/assets/{app_name}/dist/css-rtl/{f.name}"

        self._merge_json(assets_dir / "assets.json", assets)
        if rtl_assets:
            self._merge_json(assets_dir / "assets-rtl.json", rtl_assets)

    @staticmethod
    def _merge_json(path: Path, new_entries: dict) -> None:
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
        existing.update(new_entries)
        path.write_text(json.dumps(existing, indent="\t", sort_keys=True) + "\n")

    def _ensure_uv(self) -> str:
        uv = shutil.which("uv")
        if uv:
            return uv

        print("uv not found — installing via official installer...", flush=True)
        try:
            run_command(
                ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                stream_output=True,
            )
        except Exception:
            print("curl installer failed — falling back to pip install uv...", flush=True)
            run_command(
                [sys.executable, "-m", "pip", "install", "--user", "uv"],
                stream_output=True,
            )

        for candidate in [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ]:
            if candidate.exists():
                return str(candidate)

        # Re-check PATH in case the shell profile was updated.
        uv = shutil.which("uv")
        if uv:
            return uv

        raise BenchError("uv was installed but cannot be found. Add ~/.local/bin to your PATH and re-run.")

    def _install_node_linux(self) -> None:
        run_command(
            ["bash", "-c", "curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -"],
            stream_output=True,
        )
        run_command(["sudo", "apt-get", "install", "-y", "nodejs"], stream_output=True)
