from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Replace each bench.toml's cleartext admin.password with a hash.

    Anything that can read bench.toml could sign in as admin before this; after it, the
    file holds only a verifier. Sessions already issued keep working.
    """
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        _hash_password(bench_dir)
        mark_applied(bench_dir, PATCH_NAME)


def _hash_password(bench_dir: Path) -> None:
    from pilot.config import BenchConfig
    from pilot.internal.password_hash import hash_password, is_hashed

    raw = BenchConfig.read_raw(bench_dir)
    password = raw.get("admin", {}).get("password", "")
    if not password or is_hashed(password):
        return
    raw["admin"]["password"] = hash_password(password)
    BenchConfig.write_raw(bench_dir, raw)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
