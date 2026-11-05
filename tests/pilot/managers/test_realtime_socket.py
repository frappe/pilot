from __future__ import annotations

from pathlib import Path

from pilot.config import (
    AppConfig,
    BenchConfig,
    LiteModeConfig,
    MariaDBConfig,
    RedisConfig,
    SiteConfig,
    WorkerConfig,
)
from pilot.core.bench import Bench
from pilot.managers.nginx import NginxConfigRenderer
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from pilot.managers.processes.systemd_render import SystemdRenderer


def make_bench(tmp_path: Path, **config) -> Bench:
    bench = Bench(
        BenchConfig(
            name="test-bench",
            python_version="3.14",
            apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
            mariadb=MariaDBConfig(root_password="root"),
            redis=RedisConfig(cache_port=13000, queue_port=11000),
            workers=WorkerConfig(),
            lite_mode=LiteModeConfig(enabled=False),
            **config,
        ),
        tmp_path,
    )
    bench.config.production.process_manager = "systemd"
    write_node_utils(bench, "if (process.env.FRAPPE_SOCKETIO_UDS) {}")
    return bench


def write_node_utils(bench: Bench, content: str) -> None:
    node_utils = bench.apps_path / "frappe" / "node_utils.js"
    node_utils.parent.mkdir(parents=True, exist_ok=True)
    node_utils.write_text(content)


def socketio(bench: Bench, dev: bool = False):
    return ProcessDefinitionBuilder(bench, Path("/env/bin/python"), False).socketio_definition(dev=dev)


def test_production_realtime_listens_on_a_unix_socket(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    socket = bench.config_path / "socketio.sock"

    definition = socketio(bench)
    unit = SystemdRenderer("test-bench").render(definition)

    assert bench.realtime_socket == socket
    assert definition.env == {"FRAPPE_SOCKETIO_UDS": str(socket)}
    assert f"ExecStartPre=/bin/rm -f {socket}\n" in unit
    assert unit.index("ExecStartPre=") < unit.index("ExecStart=")


def test_development_realtime_keeps_its_port(tmp_path: Path) -> None:
    definition = socketio(make_bench(tmp_path), dev=True)

    assert definition.env == {}
    assert "ExecStartPre" not in SystemdRenderer("test-bench").render(definition)


def test_the_python_backend_keeps_its_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, socketio_backend="python")

    assert bench.realtime_socket is None
    assert "FRAPPE_SOCKETIO_UDS" not in socketio(bench).env


def test_supervisor_keeps_the_realtime_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.process_manager = "supervisor"

    assert bench.realtime_socket is None


def nginx_config(bench: Bench) -> str:
    renderer = NginxConfigRenderer(bench)
    renderer._proxy_servers_cache = []
    site = SiteConfig(name="site1.example.com", apps=["frappe"])
    return renderer.generate_bench_config([(site, [])], admin_ssl=False)


def test_nginx_proxies_realtime_to_the_socket_before_the_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    config = nginx_config(bench)

    assert f"server unix:{bench.config_path / 'socketio.sock'} max_fails=0;\n    server 127.0.0.1:9000 backup;" in config
    assert "proxy_pass         http://realtime-test-bench;" in config


def test_nginx_keeps_both_realtime_targets_whatever_frappe_supports(tmp_path: Path) -> None:
    """The realtime unit can be written for other Frappe code than nginx; either target must answer."""
    bench = make_bench(tmp_path)
    with_socket = nginx_config(bench)
    write_node_utils(bench, "conf.socketio_port = 9000;")

    assert nginx_config(bench) == with_socket


def test_nginx_proxies_realtime_to_the_port_without_a_socket(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.process_manager = "supervisor"

    config = nginx_config(bench)

    assert "upstream realtime-" not in config
    assert "proxy_pass         http://127.0.0.1:9000;" in config


def test_a_long_socket_path_keeps_the_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path / ("é" * 48))

    assert bench.realtime_socket is None


def test_a_frappe_without_socket_support_keeps_the_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    write_node_utils(bench, "conf.socketio_port = 9000;")

    assert bench.realtime_socket is None
    assert socketio(bench).env == {}
