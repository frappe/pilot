from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Turn on the CPU, memory and disk alerts once on a host that never set any of them.

    The alerts are host-wide, so the marker is on the benches folder, not on a bench."""
    from pilot.config.alert_limit import DEFAULT_USAGE_LIMIT, RESOURCE_LIMIT_FIELDS
    from pilot.config.common import CommonConfig
    from pilot.internal.patch_state import is_applied, mark_applied

    if is_applied(benches_root, PATCH_NAME) or not CommonConfig.path(benches_root).exists():
        return
    with CommonConfig.open(benches_root) as common:
        limits = common.resource_limits
        if not any(getattr(limits, name) for name in RESOURCE_LIMIT_FIELDS):
            for name in RESOURCE_LIMIT_FIELDS:
                setattr(limits, name, DEFAULT_USAGE_LIMIT)
            print(f"Turned on the CPU, memory and disk alerts at {DEFAULT_USAGE_LIMIT}%.")
    mark_applied(benches_root, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
