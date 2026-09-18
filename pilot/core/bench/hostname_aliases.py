from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.config.central import HostnameAlias
from pilot.utils import normalize_host

if TYPE_CHECKING:
    from pilot.core.bench import Bench


def _matching(aliases: list[HostnameAlias], alias_type: str, target: str) -> list[HostnameAlias]:
    return [
        alias
        for alias in aliases
        if alias.type == alias_type and normalize_host(alias.target) == normalize_host(target)
    ]


def _replacing(aliases: list[HostnameAlias], alias: HostnameAlias) -> list[HostnameAlias]:
    """One hostname pattern resolves to one nginx vhost, so it holds one alias."""
    return [saved for saved in aliases if saved.pattern != alias.pattern] + [alias]


class HostnameAliases:
    """Central's VM hostname aliases: host-wide state, rendered per bench."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def set(self, alias_type: str, pattern: str, target: str, redirect: bool = False) -> None:
        """Write one alias, replacing whatever held the same pattern."""
        from pilot.config.common import CommonConfig

        alias = HostnameAlias(type=alias_type, pattern=pattern, target=target, redirect=redirect)
        # The file is shared, so read and write in one transaction.
        with CommonConfig.open(self.bench.path.parent) as common:
            common.central.hostname_aliases = _replacing(common.central.hostname_aliases, alias)

        # Keep the caller's in-memory config in sync for nginx rendering.
        central = self.bench.config.central
        central.hostname_aliases = _replacing(central.hostname_aliases, alias)

    def retarget(self, alias_type: str, old_target: str, new_target: str) -> bool:
        """Retarget Central aliases under the shared config lock."""
        from pilot.config.common import CommonConfig

        if normalize_host(old_target) == normalize_host(new_target):
            return False

        with CommonConfig.open(self.bench.path.parent) as common:
            saved = _matching(common.central.hostname_aliases, alias_type, old_target)
            if not saved:
                return False
            for alias in saved:
                alias.target = new_target

        for alias in _matching(self.bench.config.central.hostname_aliases, alias_type, old_target):
            alias.target = new_target
        return True
