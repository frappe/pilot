from pathlib import Path

from pilot.config.central import HostnameAlias
from pilot.config.common import CommonConfig
from pilot.core.bench import Bench
from tests.pilot.integrations.test_central_client import _bench


def _with_aliases(tmp_path: Path, *aliases: HostnameAlias) -> Bench:
    bench = _bench(tmp_path)
    common = CommonConfig.read(bench.path.parent)
    common.central.enabled = True
    common.central.hostname_aliases = list(aliases)
    common.write(bench.path.parent)
    bench.config.central.enabled = True
    bench.config.central.hostname_aliases = [HostnameAlias(**vars(alias)) for alias in aliases]
    return bench


def _targets(bench: Bench) -> list[str]:
    return [alias.target for alias in CommonConfig.read(bench.path.parent).central.hostname_aliases]


def test_a_site_alias_follows_the_renamed_site(tmp_path: Path) -> None:
    bench = _with_aliases(
        tmp_path,
        HostnameAlias(type="site", pattern="site-*.zone.test", target="old.example.com"),
    )

    assert bench.hostname_aliases.retarget("site", "old.example.com", "new.example.com") is True
    assert _targets(bench) == ["new.example.com"]
    # The caller renders nginx from the config it holds, so that must move too.
    assert [alias.target for alias in bench.config.central.hostname_aliases] == ["new.example.com"]


def test_an_admin_alias_is_left_alone_by_a_site_rename(tmp_path: Path) -> None:
    bench = _with_aliases(
        tmp_path,
        HostnameAlias(type="admin", pattern="vm-*.zone.test", target="old.example.com"),
    )

    assert bench.hostname_aliases.retarget("site", "old.example.com", "new.example.com") is False
    assert _targets(bench) == ["old.example.com"]


def test_only_the_matching_target_moves(tmp_path: Path) -> None:
    bench = _with_aliases(
        tmp_path,
        HostnameAlias(type="site", pattern="site-*.zone.test", target="old.example.com"),
        HostnameAlias(type="site", pattern="alt-*.zone.test", target="other.example.com"),
    )

    assert bench.hostname_aliases.retarget("site", "old.example.com", "new.example.com") is True
    assert _targets(bench) == ["new.example.com", "other.example.com"]


def test_renaming_to_the_same_host_writes_nothing(tmp_path: Path) -> None:
    bench = _with_aliases(
        tmp_path,
        HostnameAlias(type="site", pattern="site-*.zone.test", target="Same.example.com"),
    )

    assert bench.hostname_aliases.retarget("site", "same.example.com", "Same.example.com") is False


def test_a_host_with_no_aliases_is_a_no_op(tmp_path: Path) -> None:
    bench = _with_aliases(tmp_path)

    assert bench.hostname_aliases.retarget("site", "old.example.com", "new.example.com") is False


def test_concurrent_retargets_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """The file is shared by every bench on the host. Reading and writing as
    separate steps would let each thread write back the whole of what it read,
    losing the other's edit."""
    import threading

    bench = _with_aliases(
        tmp_path,
        HostnameAlias(type="site", pattern="site-*.zone.test", target="site-old.example.com"),
        HostnameAlias(type="admin", pattern="vm-*.zone.test", target="vm-old.example.com"),
    )
    barrier = threading.Barrier(2)

    def move(alias_type: str, old: str, new: str) -> None:
        barrier.wait()
        bench.hostname_aliases.retarget(alias_type, old, new)

    threads = [
        threading.Thread(target=move, args=("site", "site-old.example.com", "site-new.example.com")),
        threading.Thread(target=move, args=("admin", "vm-old.example.com", "vm-new.example.com")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(_targets(bench)) == ["site-new.example.com", "vm-new.example.com"]


def test_a_shared_file_edit_keeps_the_rest_of_the_file(tmp_path: Path) -> None:
    """Retargeting must not write back a stale copy of everything else."""
    from pilot.config.common import CommonConfig

    bench = _with_aliases(
        tmp_path, HostnameAlias(type="site", pattern="site-*.zone.test", target="old.example.com")
    )
    # Another bench edits an unrelated shared setting after our copy was loaded.
    with CommonConfig.open(bench.path.parent) as common:
        common.mariadb.root_password = "written-by-someone-else"

    bench.hostname_aliases.retarget("site", "old.example.com", "new.example.com")

    saved = CommonConfig.read(bench.path.parent)
    assert saved.mariadb.root_password == "written-by-someone-else"
    assert _targets(bench) == ["new.example.com"]
