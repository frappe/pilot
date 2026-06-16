from __future__ import annotations

from bench_cli.config.bench_config import BenchConfig


def bench_config_to_toml(config: BenchConfig) -> str:
    parts: list[str] = []

    parts.append("[bench]")
    parts.append(f'name = "{config.name}"')
    parts.append(f'python = "{config.python_version}"')
    parts.append(f"http_port = {config.http_port}")
    parts.append(f"socketio_port = {config.socketio_port}")
    parts.append(f'socketio_backend = "{config.socketio_backend}"')
    if config.default_branch:
        parts.append(f'default_branch = "{config.default_branch}"')
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
    if r.version:
        parts.append(f'version = "{r.version}"')
    parts.append("")

    for group in config.workers.groups:
        parts.append("[[workers]]")
        queues = ", ".join(f'"{q}"' for q in group.queues)
        parts.append(f"queues = [{queues}]")
        parts.append(f"count = {group.count}")
        parts.append("")

    p = config.production
    parts.append("[production]")
    parts.append(f'process_manager = "{p.process_manager}"')
    parts.append(f"nginx = {'true' if p.nginx else 'false'}")
    parts.append(f"use_companion_manager = {'true' if p.use_companion_manager else 'false'}")
    parts.append("")

    n = config.nginx
    parts.append("[nginx]")
    parts.append(f"http_port = {n.http_port}")
    parts.append(f"https_port = {n.https_port}")
    parts.append(f'config_dir = "{n.config_dir}"')
    parts.append(f'worker_processes = "{n.worker_processes}"')
    parts.append(f'client_max_body_size = "{n.client_max_body_size}"')
    parts.append("")

    g = config.gunicorn
    parts.append("[gunicorn]")
    parts.append(f"workers = {g.workers}")
    parts.append(f"threads = {g.threads}")
    parts.append(f"timeout = {g.timeout}")
    parts.append(f'worker_class = "{g.worker_class}"')
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
    parts.append(f'pool = "{v.pool}"')
    parts.append(f'backing = "{v.backing}"')
    if v.backing == "image":
        parts.append("")
        parts.append("[volume.image]")
        parts.append(f'size = "{v.image.size}"')
        parts.append(f'path = "{v.image_path}"')
    elif v.backing == "device":
        parts.append(f'device = "{v.device}"')
    # backing = "auto" carries no device/image fields — resolved during bench init
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

    return "\n".join(parts)
