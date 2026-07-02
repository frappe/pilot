from __future__ import annotations

from pilot.config.bench_config import BenchConfig


def bench_config_to_toml(config: BenchConfig) -> str:
    parts: list[str] = []

    parts.append("[bench]")
    parts.append(f'name = "{config.name}"')
    parts.append(f'python = "{config.python_version}"')
    parts.append(f"http_port = {config.http_port}")
    parts.append(f"socketio_port = {config.socketio_port}")
    parts.append(f'socketio_backend = "{config.socketio_backend}"')
    parts.append(f'db_type = "{config.db_type}"')
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
    if m.instance:
        parts.append(f'instance = "{m.instance}"')
    if m.data_dir:
        parts.append(f'data_dir = "{m.data_dir}"')
    parts.append("")

    pg = config.postgres
    parts.append("[postgres]")
    parts.append(f'host = "{pg.host}"')
    parts.append(f"port = {pg.port}")
    parts.append(f'root_password = "{pg.root_password}"')
    parts.append(f'admin_user = "{pg.admin_user}"')
    if pg.version:
        parts.append(f'version = "{pg.version}"')
    if pg.instance:
        parts.append(f'instance = "{pg.instance}"')
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
    parts.append(f"enabled = {'true' if p.enabled else 'false'}")
    if p.process_manager:
        parts.append(f'process_manager = "{p.process_manager}"')
    parts.append(f"use_companion_manager = {'true' if p.use_companion_manager else 'false'}")
    parts.append("")

    g = config.gunicorn
    parts.append("[gunicorn]")
    parts.append(f"workers = {g.workers}")
    parts.append(f"threads = {g.threads}")
    parts.append(f"timeout = {g.timeout}")
    parts.append(f'worker_class = "{g.worker_class}"')
    parts.append(f"malloc_arena_max = {g.malloc_arena_max or 2}")
    parts.append(f"max_requests = {g.max_requests}")
    parts.append(f"max_requests_jitter = {g.max_requests_jitter}")
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
    if a.jwt_secret:
        parts.append(f'jwt_secret = "{a.jwt_secret}"')
    parts.append(f'domain = "{a.domain}"')
    parts.append(f"tls = {'true' if a.tls else 'false'}")
    parts.append("")

    v = config.volume
    parts.append("[volume]")
    parts.append(f"enabled = {'true' if v.enabled else 'false'}")
    if v.enabled:
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
        parts.append("[volume.dataset]")
        parts.append(f'reservation = "{v.dataset.reservation}"')
        parts.append(f'quota = "{v.dataset.quota}"')
    parts.append("")

    fw = config.firewall
    if fw.enabled or fw.rules:
        parts.append("[firewall]")
        parts.append(f"enabled = {'true' if fw.enabled else 'false'}")
        parts.append(f'default = "{fw.default}"')
        parts.append("")
        for rule in fw.rules:
            parts.append("[[firewall.rules]]")
            parts.append(f'ip = "{rule.ip}"')
            parts.append(f'action = "{rule.action}"')
            parts.append(f'description = "{rule.description}"')
            parts.append("")

    # Only add monitoring section if production is enabled
    if p.enabled:
        mon = config.monitor
        parts.append("[monitor]")
        parts.append(f'system_log_path = "{mon.system_log_path}"')
        parts.append(f'authority_file_path = "{mon.authority_file_path}"')
        parts.append(f'system_log_max_size = "{mon.system_log_max_size}"')
        parts.append(f'application_log_max_size = "{mon.application_log_max_size}"')
        if mon.log_path:
            parts.append(f'log_path = "{mon.log_path}"')
        parts.append("")

    return "\n".join(parts)
