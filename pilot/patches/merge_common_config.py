from __future__ import annotations

import shutil
from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Move mariadb/postgres/letsencrypt/jwks_* from each bench.toml into one
    shared common_config.toml, then trim those fields from every bench.toml."""
    from pilot.config.common import CommonConfig
    from pilot.internal.patch_state import is_applied, mark_applied

    bench_dirs = [
        entry
        for entry in sorted(benches_root.glob("*"))
        if entry.is_dir() and (entry / "bench.toml").exists() and not is_applied(entry, PATCH_NAME)
    ]
    if not bench_dirs:
        return

    _validate_consistent_shared_config(bench_dirs)

    if not CommonConfig.path(benches_root).exists():
        _seed_common_config(benches_root, bench_dirs)

    for bench_dir in bench_dirs:
        _trim_bench_toml(bench_dir)
        mark_applied(bench_dir, PATCH_NAME)


def _validate_consistent_shared_config(bench_dirs: list[Path]) -> None:
    """Every bench that already carries one of these tables must agree with
    every other one - a single common_config.toml can no longer represent
    two different MariaDB/Postgres/Let's Encrypt/JWKS setups."""
    from pilot.config import BenchConfig
    from pilot.config.common import _known_fields
    from pilot.config.letsencrypt import LetsEncryptConfig
    from pilot.config.mariadb import MariaDBConfig
    from pilot.config.postgres import PostgresConfig

    raw_by_bench = {bench_dir.name: BenchConfig.read_raw(bench_dir) for bench_dir in bench_dirs}

    _check_agreement(raw_by_bench, "mariadb", lambda d: MariaDBConfig(**_known_fields(MariaDBConfig, d)))
    _check_agreement(raw_by_bench, "postgres", lambda d: PostgresConfig(**_known_fields(PostgresConfig, d)))
    _check_agreement(raw_by_bench, "letsencrypt", LetsEncryptConfig.from_dict)
    _check_jwks_agreement(raw_by_bench)


def _check_agreement(raw_by_bench: dict[str, dict], key: str, build) -> None:
    values = {name: build(raw[key]) for name, raw in raw_by_bench.items() if key in raw}
    if _disagrees(values):
        _raise_divergence(key, values)


def _check_jwks_agreement(raw_by_bench: dict[str, dict]) -> None:
    values = {
        name: (raw["admin"]["jwks_url"], raw["admin"].get("jwks_audience", ""))
        for name, raw in raw_by_bench.items()
        if "jwks_url" in raw.get("admin", {})
    }
    if _disagrees(values):
        _raise_divergence("admin.jwks_url/jwks_audience", values)


def _disagrees(values: dict) -> bool:
    first = next(iter(values.values()), None)
    return any(value != first for value in values.values())


def _raise_divergence(key: str, values: dict) -> None:
    from pilot.exceptions import ConfigError

    benches = ", ".join(values)
    raise ConfigError(
        f"Benches disagree on [{key}]: {benches}. Every bench on a host must now share one "
        f"common_config.toml, so multiple MariaDB/Postgres/Let's Encrypt/JWKS setups per host "
        f"are no longer supported. Edit each bench's bench.toml [{key}] to match before "
        f"re-running this patch."
    )


def _seed_common_config(benches_root: Path, bench_dirs: list[Path]) -> None:
    from pilot.config import BenchConfig
    from pilot.config.common import CommonConfig

    for bench_dir in bench_dirs:
        raw = BenchConfig.read_raw(bench_dir)
        admin = raw.get("admin", {})
        has_shared_fields = any(key in raw for key in ("mariadb", "postgres", "letsencrypt")) or (
            "jwks_url" in admin
        )
        if not has_shared_fields:
            continue
        CommonConfig.from_raw_dict(raw).write(benches_root)
        print(f"common_config.toml seeded from '{bench_dir.name}'.")
        return


def _trim_bench_toml(bench_dir: Path) -> None:
    from pilot.config import BenchConfig

    raw = BenchConfig.read_raw(bench_dir)
    changed = False
    for key in ("mariadb", "postgres", "letsencrypt"):
        if raw.pop(key, None) is not None:
            changed = True
    admin = raw.get("admin")
    if admin:
        for key in ("jwks_url", "jwks_audience"):
            if admin.pop(key, None) is not None:
                changed = True
    if not changed:
        return
    _backup_bench_toml(bench_dir)
    BenchConfig.write_raw(bench_dir, raw)


def _backup_bench_toml(bench_dir: Path) -> None:
    source = bench_dir / "bench.toml"
    backup = bench_dir / "bench.toml.bak"
    counter = 1
    while backup.exists():
        backup = bench_dir / f"bench.toml.bak.{counter}"
        counter += 1
    shutil.copy2(source, backup)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
