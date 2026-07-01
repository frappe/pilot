import subprocess
import sys

from pilot.config.toml_store import BenchTomlStore
from pilot.managers.python_env_manager import PythonEnvManager
from .base_task import BaseTask


class SwitchBranchTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        p.add_argument("branch")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name
        self.branch = args.branch

    def run(self) -> None:
        app_path = self.bench_root / "apps" / self.name
        if not (app_path / ".git").exists():
            print(f"Error: '{self.name}' is not cloned at {app_path}")
            sys.exit(1)

        self._step("fetch", f"Fetch remote branches for {self.name}")
        subprocess.run(["git", "-C", str(app_path), "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"], check=False)
        subprocess.run(["git", "-C", str(app_path), "merge", "--abort"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(app_path), "rebase", "--abort"], capture_output=True, check=False)
        stash = subprocess.run(
            ["git", "-C", str(app_path), "stash", "--include-untracked"],
            capture_output=True, text=True, check=False,
        )
        stashed = "No local changes" not in stash.stdout

        self._step("checkout", f"Switch to branch '{self.branch}'")
        result = subprocess.run(
            ["git", "-C", str(app_path), "checkout", "-B", self.branch, f"origin/{self.branch}"],
            check=False,
        )
        if result.returncode != 0:
            if stashed:
                subprocess.run(["git", "-C", str(app_path), "stash", "pop"], check=False)
            print(f"Error: could not switch to branch '{self.branch}'")
            sys.exit(result.returncode)

        uv = PythonEnvManager(self.bench)._ensure_uv()
        python_bin = str(self.bench_root / "env" / "bin" / "python")
        self._step("install", f"Reinstall {self.name}")
        subprocess.run([uv, "pip", "install", "--python", python_bin, "-e", str(app_path)], check=False)

        store = BenchTomlStore.for_bench(self.bench_root)
        raw = store.read_raw()
        for app_entry in raw.get("apps", []):
            if app_entry.get("name") == self.name:
                app_entry["branch"] = self.branch
                break
        store.write_raw(raw)
        print(f"Updated bench.toml: {self.name} -> {self.branch}")

        if (app_path / "package.json").exists():
            self._step("js", f"Install JS dependencies for {self.name}")
            subprocess.run(["yarn", "install"], cwd=str(app_path), check=False)

        self._step("assets", "Build assets")
        subprocess.run([*self.bench.frappe_call, "frappe", "build", "--force"], cwd=str(self.bench.sites_path), check=False)
        print(f"'{self.name}' switched to '{self.branch}' successfully.")
        self._step("done")


if __name__ == "__main__":
    SwitchBranchTask.main()
