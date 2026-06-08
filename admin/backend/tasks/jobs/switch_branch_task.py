import subprocess
import sys
import tomllib

from bench_cli.managers.python_env_manager import PythonEnvManager
from bench_cli.utils import write_toml
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

        print(f"Fetching all remote branches for {self.name}...")
        sys.stdout.flush()
        subprocess.run(["git", "-C", str(app_path), "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"], check=False)
        subprocess.run(["git", "-C", str(app_path), "merge", "--abort"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(app_path), "rebase", "--abort"], capture_output=True, check=False)
        stash = subprocess.run(
            ["git", "-C", str(app_path), "stash", "--include-untracked"],
            capture_output=True, text=True, check=False,
        )
        stashed = "No local changes" not in stash.stdout

        print(f"Switching to branch '{self.branch}'...")
        sys.stdout.flush()
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
        print(f"Reinstalling {self.name}...")
        sys.stdout.flush()
        subprocess.run([uv, "pip", "install", "--python", python_bin, "-e", str(app_path)], check=False)

        bench_toml = self.bench_root / "bench.toml"
        with bench_toml.open("rb") as fh:
            raw = tomllib.load(fh)
        for app_entry in raw.get("apps", []):
            if app_entry.get("name") == self.name:
                app_entry["branch"] = self.branch
                break
        write_toml(bench_toml, raw)
        print(f"Updated bench.toml: {self.name} -> {self.branch}")
        sys.stdout.flush()

        if (app_path / "package.json").exists():
            print(f"\nInstalling JS dependencies for {self.name}...")
            sys.stdout.flush()
            subprocess.run(["yarn", "install"], cwd=str(app_path), check=False)

        print("\nBuilding assets...")
        sys.stdout.flush()
        subprocess.run([*self.bench.frappe_call, "frappe", "build", "--force"], cwd=str(self.bench.sites_path), check=False)
        print(f"\n'{self.name}' switched to '{self.branch}' successfully.")


if __name__ == "__main__":
    SwitchBranchTask.main()
