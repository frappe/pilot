"""Config rendering benchmarks: gunicorn, redis, nginx, systemd, supervisor, Procfile."""

from __future__ import annotations

from benchmark.fixtures import Fixture
from benchmark.harness import Case
from pilot.core.bench.config_files import BenchConfigFiles
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.nginx import NginxConfigRenderer, NginxManager
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from pilot.managers.processes.local import ProcessManager
from pilot.managers.processes.supervisor import SupervisorRenderer
from pilot.managers.processes.systemd_render import SystemdRenderer
from pilot.managers.redis import RedisManager


def _sites_for_render(fixture: Fixture):
    bench = fixture.bench()
    return [(site.config, []) for site in bench.sites()]


def _definitions(fixture: Fixture):
    bench = fixture.bench()
    return ProcessDefinitionBuilder(bench, bench.python, bench.config.watch_admin_js).process_definitions()


CASES = [
    Case(
        name="template.gunicorn",
        group="templates",
        description="GunicornManager.generate_config(): render and write gunicorn.conf.py",
        fn=lambda fixture: GunicornManager(fixture.bench()).generate_config(),
        setup=lambda fixture: (fixture.bench_root / "config" / "gunicorn.conf.py").unlink(missing_ok=True),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="template.redis",
        group="templates",
        description="RedisManager.generate_configs(): render and write both redis configs",
        fn=lambda fixture: RedisManager(fixture.bench().config.redis, fixture.bench()).generate_configs(),
        setup=lambda fixture: _remove_configs(fixture, "redis_cache.conf", "redis_queue.conf"),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="template.common_site_config",
        group="templates",
        description="BenchConfigFiles.write_common_site_config(): merge redis/worker settings",
        fn=lambda fixture: BenchConfigFiles(fixture.bench()).write_common_site_config(),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="template.nginx_render",
        group="templates",
        description="NginxConfigRenderer.generate_bench_config(): vhosts, aliases, WAF context",
        fn=lambda fixture: NginxConfigRenderer(fixture.bench()).generate_bench_config(
            _sites_for_render(fixture), False
        ),
        iterations=20,
        warmup=3,
        profile=True,
    ),
    Case(
        name="template.nginx_generate",
        group="templates",
        description="NginxManager.generate_config(): error pages, WAF files, and bench config",
        fn=lambda fixture: NginxManager(fixture.bench()).generate_config(),
        setup=lambda fixture: _remove_tree(fixture.bench_root / "config" / "nginx"),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="template.systemd_units",
        group="templates",
        description="SystemdRenderer: render every process unit plus the target",
        fn=lambda fixture: _render_systemd(fixture),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="template.supervisor_conf",
        group="templates",
        description="SupervisorRenderer.render_supervisord_conf(): groups and program blocks",
        fn=lambda fixture: _render_supervisor(fixture),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="template.procfile",
        group="templates",
        description="ProcessManager.write_config(): definitions, redis, gunicorn, Procfile",
        fn=lambda fixture: ProcessManager(fixture.bench()).write_config(),
        setup=lambda fixture: _remove_configs(
            fixture, "Procfile", "redis_cache.conf", "redis_queue.conf", "gunicorn.conf.py"
        ),
        iterations=20,
        warmup=3,
    ),
]


def _render_systemd(fixture: Fixture) -> str:
    bench = fixture.bench()
    renderer = SystemdRenderer(bench.config.name)
    definitions = _definitions(fixture)
    units = [renderer.render(pd) for pd in definitions]
    return renderer.render_target(units)


def _render_supervisor(fixture: Fixture) -> str:
    bench = fixture.bench()
    renderer = SupervisorRenderer(bench.config.name, bench.logs_path)
    return renderer.render_supervisord_conf(
        _definitions(fixture), bench.config_path / "supervisord.sock", bench.config_path / "supervisord.pid"
    )


def _remove_configs(fixture: Fixture, *names: str) -> None:
    config = fixture.bench_root / "config"
    for name in names:
        (config / name).unlink(missing_ok=True)


def _remove_tree(path) -> None:
    import shutil

    if path.exists():
        shutil.rmtree(path)
