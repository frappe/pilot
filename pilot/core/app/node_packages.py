from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.utils import run_command


@dataclass(frozen=True)
class NodePackageManager:
    """A JS package manager, known by the lockfile it writes."""

    name: str
    lockfile: str
    install_marker: str
    """A file the manager rewrites in node_modules on every install."""
    extra_install_args: tuple[str, ...] = ()

    @property
    def local_bin(self) -> Path:
        return Path.home() / ".local" / "bin" / self.name

    @property
    def is_installed(self) -> bool:
        return bool(shutil.which(self.name)) or self.local_bin.exists()

    @property
    def bin(self) -> str:
        """The tool on PATH, or where `ensure_installed` puts it."""
        if not self.is_installed:
            raise BenchError(f"{self.name} not found on PATH or in {self.local_bin.parent}.")
        return shutil.which(self.name) or str(self.local_bin)

    def ensure_installed(self) -> None:
        """Install the tool into ~/.local unless it is already installed."""
        if self.is_installed:
            return
        prefix = Path.home() / ".local"
        prefix.mkdir(parents=True, exist_ok=True)
        run_command(["npm", "install", "-g", self.name, "--prefix", str(prefix)])


YARN = NodePackageManager(
    name="yarn",
    lockfile="yarn.lock",
    install_marker=".yarn-integrity",
)
PNPM = NodePackageManager(
    name="pnpm",
    lockfile="pnpm-lock.yaml",
    install_marker=".modules.yaml",
    # Without a TTY, pnpm stops instead of asking to replace a node_modules another manager wrote.
    extra_install_args=("--config.confirmModulesPurge=false",),
)
PACKAGE_MANAGERS = (YARN, PNPM)


class NodePackages:
    """JS dependencies of a package directory, installed by the manager its lockfile names.

    A member of the workspace at `app_path` without its own lockfile installs from that root.
    A directory without any lockfile installs with yarn, as Frappe does.
    """

    def __init__(self, path: Path, app_path: Path | None = None) -> None:
        self.path = path
        self.app_path = app_path or path

    @property
    def install_path(self) -> Path:
        if self.lockfiles_in(self.path) or self.path == self.app_path:
            return self.path
        if self.is_workspace_member:
            return self.app_path
        return self.path

    @property
    def is_workspace_member(self) -> bool:
        """Whether the workspace at `app_path` lists `path`.

        A pnpm root lists its members as importers in its lockfile. Other roots list them in
        `package.json` workspaces, where a `!` pattern excludes a match.
        """
        member = self.path.relative_to(self.app_path).as_posix()
        pnpm_lockfile = self.app_path / PNPM.lockfile
        if pnpm_lockfile.exists():
            return member in self.read_pnpm_importers(pnpm_lockfile.read_text())

        patterns = [pattern.strip() for pattern in self.workspace_patterns]

        def matches(pattern: str) -> bool:
            return fnmatch(member, pattern.removeprefix("./").rstrip("/"))

        return any(matches(pattern) for pattern in patterns if not pattern.startswith("!")) and not any(
            matches(pattern[1:].strip()) for pattern in patterns if pattern.startswith("!")
        )

    @property
    def workspace_patterns(self) -> list[str]:
        """Member globs from `package.json` workspaces at `app_path`."""
        package_json = self.app_path / "package.json"
        if not package_json.exists():
            return []
        workspaces = json.loads(package_json.read_text()).get("workspaces", [])
        return workspaces.get("packages", []) if isinstance(workspaces, dict) else workspaces

    def validate_lockfiles(self) -> None:
        """Raise when the install path has the lockfiles of two package managers."""
        lockfiles = self.lockfiles_in(self.install_path)
        if len(lockfiles) > 1:
            names = " and ".join(lockfile.name for lockfile in lockfiles)
            raise BenchError(
                f"{self.install_path} has both {names}. Keep the lockfile of one package manager."
            )

    @property
    def lockfile(self) -> Path | None:
        self.validate_lockfiles()
        lockfiles = self.lockfiles_in(self.install_path)
        return lockfiles[0] if lockfiles else None

    @property
    def package_manager(self) -> NodePackageManager:
        lockfile = self.lockfile
        if lockfile is None:
            return YARN
        return next(manager for manager in PACKAGE_MANAGERS if manager.lockfile == lockfile.name)

    @property
    def is_installed(self) -> bool:
        """Whether node_modules matches the dependency inputs."""
        manager = self.package_manager
        marker = self.install_path / "node_modules" / manager.install_marker
        lockfile = self.lockfile
        if not marker.exists():
            return False
        if manager == YARN and lockfile is not None:
            from pilot.managers.node_dependencies import NodeDependencies

            return NodeDependencies.has_matching_install(
                self.install_path, NodeDependencies.get_key(self.install_path)
            )
        return lockfile is None or lockfile.stat().st_mtime <= marker.stat().st_mtime

    @property
    def has_package_json(self) -> bool:
        return (self.path / "package.json").exists()

    def install(self, frozen: bool = True) -> None:
        """Install the dependencies. A directory without package.json has none."""
        if not self.has_package_json:
            return
        manager = self.package_manager
        if self.lockfile is None:
            print(f"  {self.install_path} has no lockfile; {manager.name} installs unpinned versions.")
            sys.stdout.flush()
        manager.ensure_installed()
        frozen_args = ["--frozen-lockfile"] if frozen else []
        run_command(
            [manager.bin, "install", *frozen_args, *manager.extra_install_args],
            cwd=self.install_path,
            stream_output=True,
        )
        if manager == YARN:
            from pilot.managers.node_dependencies import NodeDependencies

            if NodeDependencies.get_resolved_key(self.install_path):
                stamp = self.install_path / "node_modules" / ".pilot-install-key"
                stamp.write_text(NodeDependencies.get_key(self.install_path))

    def ensure_installed(self) -> None:
        if self.has_package_json and not self.is_installed:
            print(f"  Installing JS dependencies in {self.install_path}...")
            sys.stdout.flush()
            self.install()

    def get_script_command(self, script: str) -> list[str]:
        """The command that runs a package.json script in `path`."""
        manager = self.package_manager
        manager.ensure_installed()
        return [manager.bin, "run", script]

    @property
    def has_build_script(self) -> bool:
        if not self.has_package_json:
            return False
        return "build" in json.loads((self.path / "package.json").read_text()).get("scripts", {})

    @staticmethod
    def lockfiles_in(path: Path) -> list[Path]:
        return [path / manager.lockfile for manager in PACKAGE_MANAGERS if (path / manager.lockfile).exists()]

    @staticmethod
    def read_pnpm_importers(text: str) -> set[str]:
        """Keys of every top-level `importers:` map in a pnpm lockfile. Pilot has no YAML parser.

        pnpm can write more than one YAML document to the lockfile, each with its own importers.
        """
        importers: set[str] = set()
        in_importers = False
        for line in text.splitlines():
            if line and not line[0].isspace():
                in_importers = line.rstrip() == "importers:"
            elif in_importers and len(line) - len(line.lstrip()) == 2:
                key = line.strip().partition(":")[0]
                importers.add(key.strip("'\""))
        return importers


def get_app_packages(app_path: Path, package_dirs: list[Path]) -> list[NodePackages]:
    """The packages of the app root and `package_dirs`, one per install path.

    Checks every lockfile first, so an app with a lockfile conflict installs nothing.
    """
    packages_by_install_path: dict[Path, NodePackages] = {}
    for path in [app_path, *package_dirs]:
        packages = NodePackages(path, app_path)
        if packages.has_package_json:
            packages_by_install_path.setdefault(packages.install_path, packages)

    for packages in packages_by_install_path.values():
        packages.validate_lockfiles()
    return list(packages_by_install_path.values())


def install_app_packages(app_path: Path, build_dirs: list[str]) -> None:
    """Install the JS dependencies of the app root and of each build dir."""
    for packages in get_app_packages(app_path, [app_path / build_dir for build_dir in build_dirs]):
        packages.install()


if __name__ == "__main__":
    # Invoked by scripts/build-app-assets.sh as: <app path> [<build dir>...]
    try:
        install_app_packages(Path(sys.argv[1]), sys.argv[2:])
    except BenchError as error:
        sys.exit(f"error: {error}")
