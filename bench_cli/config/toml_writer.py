from __future__ import annotations

from bench_cli.config.bench_config import BenchConfig


def bench_config_to_toml(config: BenchConfig) -> str:
    parts: list[str] = []

    parts.append("[bench]")
    parts.append(f'name = "{config.name}"')
    parts.append(f'python = "{config.python_version}"')
    parts.append(f"http_port = {config.http_port}")
    parts.append(f"socketio_port = {config.socketio_port}")
    parts.append("")

    for app in config.apps:
        parts.append("[[apps]]")
        parts.append(f'name = "{app.name}"')
        parts.append(f'repo = "{app.repo}"')
        parts.append(f'branch = "{app.branch}"')
        if app.branches:
            branches_str = ", ".join(f'"{b}"' for b in app.branches)
            parts.append(f"branches = [{branches_str}]")
        parts.append("")

    m = config.mariadb
    parts.append("[mariadb]")
    parts.append(f'host = "{m.host}"')
    parts.append(f"port = {m.port}")
    parts.append(f'root_password = "{m.root_password}"')
    parts.append(f'admin_user = "{m.admin_user}"')
    parts.append(f'socket_path = "{m.socket_path}"')
    if m.version:
        parts.append(f'version = "{m.version}"')
    parts.append("")

    r = config.redis
    parts.append("[redis]")
    parts.append(f"cache_port = {r.cache_port}")
    parts.append(f"queue_port = {r.queue_port}")
    parts.append(f"socketio_port = {r.socketio_port}")
    if r.version:
        parts.append(f'version = "{r.version}"')
    parts.append("")

    w = config.workers
    parts.append("[workers]")
    parts.append(f"default = {w.default_count}")
    parts.append(f"short = {w.short_count}")
    parts.append(f"long = {w.long_count}")
    for entry in w.custom:
        parts.append("")
        parts.append("[[workers.custom]]")
        parts.append(f'queue = "{entry.queue}"')
        parts.append(f"count = {entry.count}")
        parts.append(f"timeout = {entry.timeout}")
    parts.append("")

    p = config.production
    parts.append("[production]")
    parts.append(f'process_manager = "{p.process_manager}"')
    parts.append(f"nginx = {'true' if p.nginx else 'false'}")
    parts.append("")

    n = config.nginx
    parts.append("[nginx]")
    parts.append(f"http_port = {n.http_port}")
    parts.append(f"https_port = {n.https_port}")
    parts.append(f'config_dir = "{n.config_dir}"')
    parts.append(f'worker_processes = "{n.worker_processes}"')
    parts.append(f'client_max_body_size = "{n.client_max_body_size}"')
    parts.append("")

    le = config.letsencrypt
    parts.append("[letsencrypt]")
    parts.append(f'email = "{le.email}"')
    parts.append(f'webroot_path = "{le.webroot_path}"')
    parts.append("")

    a = config.admin
    parts.append("[admin]")
    parts.append(f"port = {a.port}")
    parts.append(f"timeout = {a.timeout}")
    parts.append(f"enabled = {'true' if a.enabled else 'false'}")
    parts.append(f'password = "{a.password}"')
    parts.append(f'domain = "{a.domain}"')
    parts.append("")

    v = config.volume
    parts.append("[volume]")
    parts.append(f"enabled = {'true' if v.enabled else 'false'}")
    parts.append(f'pool = "{v.pool}"')
    parts.append(f'device = "{v.device}"')
    parts.append("")
    parts.append("[volume.benches]")
    parts.append(f'reservation = "{v.benches.reservation}"')
    parts.append(f'quota = "{v.benches.quota}"')
    parts.append(f'data_dir = "{v.benches.data_dir}"')
    parts.append("")
    parts.append("[volume.mariadb]")
    parts.append(f'reservation = "{v.mariadb.reservation}"')
    parts.append(f'quota = "{v.mariadb.quota}"')
    parts.append(f'data_dir = "{v.mariadb.data_dir}"')
    parts.append("")
    parts.append("[volume.snapshots]")
    parts.append(f"enabled = {'true' if v.snapshots.enabled else 'false'}")
    parts.append("")

    return "\n".join(parts)
