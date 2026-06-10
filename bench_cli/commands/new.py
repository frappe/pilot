import secrets
from pathlib import Path

from bench_cli.config.bench_toml_builder import BenchTomlBuilder
from bench_cli.exceptions import BenchError

_BRANCH_CHOICES = ["version-16", "develop"]


class NewCommand:
    def __init__(self, target_directory: Path, name: str) -> None:
        self.target_directory = target_directory
        self.name = name

    def run(self) -> None:
        bench_toml = self.target_directory / "bench.toml"
        if bench_toml.exists():
            raise BenchError(f"A bench named '{self.name}' already exists at {self.target_directory}. Choose a different name or remove the existing bench.")

        benches_dir = self.target_directory.parent
        if not benches_dir.exists():
            print(f"Creating benches directory at {benches_dir}")
            benches_dir.mkdir(parents=True, exist_ok=True)

        default_branch = self._prompt_branch()

        print(f"Creating bench directory: {self.target_directory}")
        self.target_directory.mkdir(parents=True, exist_ok=True)

        print("Writing bench.toml")
        settings = {
            "admin_password": secrets.token_hex(nbytes=5),
            "default_branch": default_branch,
        }
        bench_toml.write_text(BenchTomlBuilder(self.name, settings).render())

        print(f"\nBench '{self.name}' created at {self.target_directory}")
        print("\nNext step:")
        print("  bench start")
        print("  Open http://localhost:8002 — the setup wizard guides you through the rest,")
        print("  including ZFS volume management under Advanced settings.")

    def _prompt_branch(self) -> str:
        choices = _BRANCH_CHOICES
        print("\nWhich default branch should this bench use?")
        for i, choice in enumerate(choices, 1):
            print(f"  {i}. {choice}")
        while True:
            raw = input(f"Enter branch name or number [1]: ").strip()
            if not raw:
                return choices[0]
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(choices):
                    return choices[idx]
                print(f"  Please enter a number between 1 and {len(choices)}.")
            else:
                return raw
