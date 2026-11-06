import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main() -> None:
    """Demonstrate doctor against real disposable files, with no Frappe installation."""
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository))
    from pilot.config import BenchConfig

    directory = Path(tempfile.mkdtemp(prefix="pilot-doctor-demo-"))
    bench = directory / "bench"
    bench.mkdir()
    BenchConfig.from_flat("demo", {"db_type": "sqlite"}).write(bench)
    venv.EnvBuilder(with_pip=False).create(bench / "env")
    app = bench / "apps" / "frappe"
    (app / "frappe").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(app)], check=True)
    (app / "frappe" / "hooks.py").write_text("raise RuntimeError('Do not import app code')\n")
    site = bench / "sites" / "demo.localhost"
    site.mkdir(parents=True)
    (site.parent / "apps.txt").write_text("frappe\n")
    (site.parent / "common_site_config.json").write_text('{"db_type": "sqlite"}')
    config = site / "site_config.json"
    config.write_text('{"db_name": "demo"}')
    environment = dict(os.environ, PYTHONPATH=str(repository))
    command = [sys.executable, "-c", "from pilot.cli import main; main()", "doctor", "--path", str(bench)]

    print(f"Demo directory (kept for inspection): {directory}", flush=True)
    print("This is a local file fixture, not an installed or running Frappe bench.", flush=True)
    for stage, expected in (("clean", 0), ("broken site JSON", 1), ("repaired", 0)):
        config.write_text("{" if expected else '{"db_name": "demo"}')
        print(f"\n--- {stage} ---", flush=True)
        result = subprocess.run(command, env=environment, check=False)
        print(f"Exit status: {result.returncode}", flush=True)
        if result.returncode != expected:
            raise SystemExit("Unexpected doctor result")
    result = subprocess.run([*command, "--json"], env=environment, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    print(f"\nJSON parsed: schema_version={report['schema_version']}, ok={report['ok']}", flush=True)
    print(f"Inspect it yourself: pilot doctor --path {bench}", flush=True)


if __name__ == "__main__":
    main()
