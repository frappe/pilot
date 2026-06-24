import io
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from bench_cli.exceptions import BenchError, CommandError

if TYPE_CHECKING:
    from bench_cli.core.bench import BenchConfig


def iter_sibling_benches(bench_path: Path) -> Iterator[tuple[Path, "BenchConfig"]]:
    """Yield ``(bench_dir, parsed bench.toml)`` for every *other* bench that
    shares this bench's parent ``benches/`` directory.

    Parse-only (no validation) so half-configured benches are still seen.
    Skips ``bench_path`` itself and any directory without a readable
    ``bench.toml``. ``bench_path`` need not exist yet (e.g. during ``bench new``).
    """
    import tomllib

    from bench_cli.core.bench import BenchConfig

    parent = bench_path.parent
    if not parent.is_dir():
        return
    me = bench_path.resolve()
    for sibling in parent.iterdir():
        if not sibling.is_dir() or sibling.resolve() == me:
            continue
        toml_path = sibling / "bench.toml"
        if not toml_path.exists():
            continue
        try:
            # Parse-only (no validate) so half-configured siblings are still
            # seen — important for port-offset collision avoidance and
            # cross-bench hostname checks.
            yield sibling, BenchConfig._from_dict(tomllib.loads(toml_path.read_text()))
        except Exception:
            continue


def normalize_host(host: str) -> str:
    """Canonical form for hostname comparison: lowercased, trailing dot stripped,
    internationalized labels reduced to their ASCII (IDNA) form. Returns an empty
    string for falsy input. Best-effort — a name that cannot be IDNA-encoded is
    returned lowercased/stripped so comparison still works for ASCII domains."""
    if not host:
        return ""
    h = host.strip().lower().rstrip(".")
    try:
        h = h.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        pass
    return h


def wildcard_suffix(pattern: str) -> str:
    """The fixed part of a wildcard domain pattern, e.g. '*.example.com' -> '.example.com',
    '*-box1.example.com' -> '-box1.example.com'."""
    return pattern[1:] if pattern.startswith("*") else pattern


def matches_wildcard(domain: str, patterns: list[str]) -> bool:
    """Whether ``domain`` ends with the fixed part of one of the wildcard ``patterns``
    and has something before it (a bare suffix with no label doesn't match)."""
    domain = normalize_host(domain)
    return any(domain != (suffix := wildcard_suffix(p)) and domain.endswith(suffix) for p in patterns)


def _bench_hosts(bench_dir: Path, config: "BenchConfig") -> Iterator[str]:
    """Yield every hostname a bench claims: its admin domain, each site's name,
    and each site's configured ``domains`` aliases — all normalized."""
    import json

    if config.admin.domain:
        yield normalize_host(config.admin.domain)
    sites_dir = bench_dir / "sites"
    if not sites_dir.is_dir():
        return
    for site in sites_dir.iterdir():
        cfg = site / "site_config.json"
        if not cfg.exists():
            continue
        yield normalize_host(site.name)
        try:
            for alias in json.loads(cfg.read_text()).get("domains", []) or []:
                name = alias.get("domain") if isinstance(alias, dict) else alias
                if name:
                    yield normalize_host(str(name))
        except Exception:
            continue


def host_owner(bench_path: Path, host: str) -> Optional[str]:
    """Return the name of *another* bench that already claims ``host`` — as one of
    its sites (name or alias) or as its ``admin.domain`` — or ``None`` if the host
    is free across all sibling benches.

    Hosts are compared in normalized form (lowercase, no trailing dot, IDNA), so
    two benches can never fight over the same hostname served by the same nginx.
    """
    target = normalize_host(host)
    if not target:
        return None
    for sibling, config in iter_sibling_benches(bench_path):
        if target in _bench_hosts(sibling, config):
            return config.name
    return None


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
        scalars = {k: v for k, v in obj.items() if not isinstance(v, (dict, list)) or (isinstance(v, list) and not any(isinstance(i, dict) for i in v))}
        dicts = {k: v for k, v in obj.items() if isinstance(v, dict)}
        array_of_tables = {k: v for k, v in obj.items() if isinstance(v, list) and any(isinstance(i, dict) for i in v)}

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


def git_has_local_changes(path: Path) -> bool:
    """True if the repo at *path* has uncommitted edits or commits not yet on upstream."""
    import subprocess

    r = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=path,
    )
    if r.returncode == 0 and r.stdout.strip():
        return True

    r = subprocess.run(
        ["git", "rev-list", "--count", "@{u}..HEAD"],
        capture_output=True, text=True, cwd=path,
    )
    return r.returncode == 0 and r.stdout.strip() not in ("", "0")


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
