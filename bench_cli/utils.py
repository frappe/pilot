import io
import shutil
import subprocess
from pathlib import Path

from bench_cli.exceptions import BenchError, CommandError


def write_toml(path: Path, data: dict) -> None:
    """Minimal TOML serialiser for the simple structures in bench.toml."""
    out = io.StringIO()

    def _write_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return f'"{v}"'
        if isinstance(v, list):
            return "[" + ", ".join(_write_value(i) for i in v) + "]"
        return str(v)

    def _write_section(obj: dict, prefix: str = "") -> None:
        scalars = {k: v for k, v in obj.items() if not isinstance(v, (dict, list)) or
                   (isinstance(v, list) and not any(isinstance(i, dict) for i in v))}
        dicts = {k: v for k, v in obj.items() if isinstance(v, dict)}
        array_of_tables = {k: v for k, v in obj.items()
                           if isinstance(v, list) and any(isinstance(i, dict) for i in v)}

        for k, v in scalars.items():
            out.write(f"{k} = {_write_value(v)}\n")

        for k, v in dicts.items():
            out.write(f"\n[{prefix}{k}]\n")
            _write_section(v, prefix=f"{prefix}{k}.")

        for k, entries in array_of_tables.items():
            for entry in entries:
                out.write(f"\n[[{prefix}{k}]]\n")
                for ek, ev in entry.items():
                    out.write(f"{ek} = {_write_value(ev)}\n")

    _write_section(data)
    path.write_text(out.getvalue())


def get_yarn_bin() -> str:
    if yarn := shutil.which("yarn"):
        return yarn
    local_yarn = Path.home() / ".local" / "bin" / "yarn"
    if local_yarn.exists():
        return str(local_yarn)
    raise BenchError("yarn not found — run bench init to install it.")


def run_command(
    argv: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    stream_output: bool = False,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=not stream_output,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode() if not stream_output and result.stderr else ""
        raise CommandError(
            f"Command {argv[0]!r} failed with exit code {result.returncode}.\n{stderr}".strip(),
            returncode=result.returncode,
        )
    return result
