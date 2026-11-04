from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Move enabled automatic updates to midnight UTC."""
    import pilot
    from pilot.config.common import CommonConfig
    from pilot.core.server import Server
    from pilot.internal.patch_state import is_applied, mark_applied

    if pilot.is_dev_build or is_applied(benches_root, PATCH_NAME):
        return
    benches_root.mkdir(parents=True, exist_ok=True)
    if CommonConfig.read(benches_root).updates.auto_pilot_update:
        Server().auto_update.enable()
    mark_applied(benches_root, PATCH_NAME)


if __name__ == "__main__":
    from pilot.utils import benches_dir

    run(benches_dir())
