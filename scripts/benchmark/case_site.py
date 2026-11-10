"""Site config, app listing, domains, and backup retention benchmarks."""

from __future__ import annotations

from benchmark.harness import Case
from pilot.core.site.config import read_site_config

CASES = [
    Case(
        name="site.read_config",
        group="site",
        description="read_site_config(): parse one site_config.json",
        fn=lambda fixture: read_site_config(fixture.bench_root / "sites" / "site1.localhost"),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="site.public_config",
        group="site",
        description="site.public_config(): protected-key filtering and secret masking",
        fn=lambda fixture: fixture.bench().sites()[0].public_config(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="site.maintenance_settings",
        group="site",
        description="site.maintenance_settings: read maintenance and scheduler flags",
        fn=lambda fixture: fixture.bench().sites()[0].maintenance_settings,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="site.installed_apps",
        group="site",
        description="site.installed_apps(): installed apps from site_config.json (no DB)",
        fn=lambda fixture: fixture.bench().sites()[0].installed_apps(),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="site.active_apps",
        group="site",
        description="site.active_apps(): installed apps minus the site's disabled list (DB query)",
        fn=lambda fixture: fixture.bench().sites()[0].active_apps(),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="site.config_all_domains",
        group="site",
        description="site.config.all_domains: site name plus configured domains",
        fn=lambda fixture: fixture.bench().sites()[0].config.all_domains,
        iterations=50,
        warmup=5,
    ),
    Case(
        name="site.set_config_values",
        group="site",
        description="site.set_config_values(): locked read-modify-write of site_config.json",
        fn=lambda fixture: fixture.bench().sites()[0].set_config_values({"maintenance_mode": 1}),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="site.domains_names",
        group="site",
        description="site.domains.names(): domains from site_config.json",
        fn=lambda fixture: fixture.bench().sites()[0].domains.names(),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="site.domains_describe",
        group="site",
        description="site.domains.describe(): domain rows with primary and route state",
        fn=lambda fixture: fixture.bench().sites()[0].domains.describe(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="site.backups_prune",
        group="site",
        description="site.backups.prune(): retention policy over local backup timestamps",
        fn=lambda fixture: fixture.bench().sites()[0].backups.prune(),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="site.backups_estimated_size",
        group="site",
        description="site.backups.get_estimated_size(): du over file trees plus newest dump size",
        fn=lambda fixture: fixture.bench().sites()[0].backups.get_estimated_size(),
        iterations=20,
        warmup=3,
    ),
]
