"""Bench inventory, git accessors, site lookup, and app validation benchmarks."""

from __future__ import annotations

from benchmark.harness import Case
from pilot.core.bench import Bench
from pilot.internal.git import GitRepo
from pilot.utils import host_owner

CASES = [
    Case(
        name="inventory.bench_init",
        group="inventory",
        description="Bench(path): resolve the path and read bench.toml",
        fn=lambda fixture: Bench(fixture.bench_root),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="inventory.apps",
        group="inventory",
        description="bench.apps(): scan apps/ and ask git for each remote and branch",
        fn=lambda fixture: Bench(fixture.bench_root).apps(),
        iterations=20,
        warmup=3,
        profile=True,
    ),
    Case(
        name="inventory.app",
        group="inventory",
        description="bench.app(name): one app's git remote and branch",
        fn=lambda fixture: Bench(fixture.bench_root).app("app1"),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="inventory.sites",
        group="inventory",
        description="bench.sites(): read every site_config.json",
        fn=lambda fixture: Bench(fixture.bench_root).sites(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="inventory.registered_apps",
        group="inventory",
        description="bench.registered_apps(): read sites/apps.txt",
        fn=lambda fixture: Bench(fixture.bench_root).registered_apps(),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="inventory.is_app_installed",
        group="inventory",
        description="bench.is_app_installed(name): resolve the module name and read apps.txt",
        fn=lambda fixture: Bench(fixture.bench_root).is_app_installed("app1"),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="git.branch",
        group="inventory",
        description="GitRepo.branch: one `git branch --show-current` subprocess",
        fn=lambda fixture: GitRepo(fixture.bench_root / "apps" / "frappe").branch,
        iterations=50,
        warmup=5,
    ),
    Case(
        name="git.remote_url",
        group="inventory",
        description="GitRepo.remote_url: one `git remote get-url` subprocess",
        fn=lambda fixture: GitRepo(fixture.bench_root / "apps" / "frappe").remote_url,
        iterations=50,
        warmup=5,
    ),
    Case(
        name="git.changed_paths",
        group="inventory",
        description="GitRepo.changed_paths: `git status --porcelain`",
        fn=lambda fixture: GitRepo(fixture.bench_root / "apps" / "frappe").changed_paths,
        iterations=30,
        warmup=3,
    ),
    Case(
        name="app.validate",
        group="inventory",
        description="app.validate(): every validator check, including bench-python AST subprocesses",
        fn=lambda fixture: Bench(fixture.bench_root).app("app1").validate(),
        iterations=10,
        warmup=2,
        profile=True,
    ),
    Case(
        name="app.process_definitions",
        group="inventory",
        description="app.processes.get_definitions(): parse [tool.pilot.processes]",
        fn=lambda fixture: Bench(fixture.bench_root).app("app1").processes.get_definitions(),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="site.resolve_name",
        group="inventory",
        description="bench.resolve_site_name(host): path check, then claim scan",
        fn=lambda fixture: Bench(fixture.bench_root).resolve_site_name("site1.localhost"),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="site.claiming",
        group="inventory",
        description="bench.site_claiming(host): pending aliases plus every site's domains",
        fn=lambda fixture: Bench(fixture.bench_root).site_claiming("site1.localhost"),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="site.certificate_name",
        group="inventory",
        description="bench.certificate_name(site): claim scan plus sibling host ownership",
        fn=lambda fixture: Bench(fixture.bench_root).certificate_name(
            Bench(fixture.bench_root).sites()[0].config
        ),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="utils.host_owner",
        group="inventory",
        description="host_owner(): scan sibling benches and their site configs",
        fn=lambda fixture: host_owner(fixture.bench_root, "site1.localhost"),
        iterations=20,
        warmup=3,
    ),
]
