from __future__ import annotations

from pilot.config.bench_config import BenchConfig
from pilot.config.waf_config import WafConfig


def _toml_string(value: str) -> str:
    """A TOML basic string with backslashes and quotes escaped, so raw SecLang
    exclusion lines (which contain double quotes) round-trip through tomllib."""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


def _toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def bench_config_to_toml(config: BenchConfig) -> str:
    parts: list[str] = []

    parts.append("[bench]")
    parts.append(f'name = "{config.name}"')
    parts.append(f'python = "{config.python_version}"')
    parts.append(f"http_port = {config.http_port}")
    parts.append(f"socketio_port = {config.socketio_port}")
    parts.append(f'socketio_backend = "{config.socketio_backend}"')
    parts.append(f"watch_apps_js = {'true' if config.watch_apps_js else 'false'}")
    parts.append(f"reload_python = {'true' if config.reload_python else 'false'}")
    parts.append(f"watch_admin_js = {'true' if config.watch_admin_js else 'false'}")
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
    parts.append(f"existing = {'true' if m.existing else 'false'}")
    parts.append("")

    pg = config.postgres
    parts.append("[postgres]")
    parts.append(f'host = "{pg.host}"')
    parts.append(f"port = {pg.port}")
    parts.append(f'root_password = "{pg.root_password}"')
    parts.append(f'admin_user = "{pg.admin_user}"')
    parts.append(f"existing = {'true' if pg.existing else 'false'}")
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
    if a.jwks_url:
        parts.append(f'jwks_url = "{a.jwks_url}"')
    if a.jwks_audience:
        parts.append(f'jwks_audience = "{a.jwks_audience}"')
    parts.append(f'domain = "{a.domain}"')
    parts.append(f"tls = {'true' if a.tls else 'false'}")
    parts.append(f"allow_bench_management = {'true' if a.allow_bench_management else 'false'}")
    parts.append("")

    c = config.central
    if c.endpoint or c.auth_token:
        parts.append("[central]")
        parts.append(f'endpoint = "{c.endpoint}"')
        parts.append(f'auth_token = "{c.auth_token}"')
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

    waf = config.waf
    # Persist whenever anything differs from the defaults — not just when enabled —
    # so non-default tuning (mode/paranoia/…) set before enabling isn't dropped on
    # the next load.
    if waf != WafConfig():
        parts.append("[waf]")
        parts.append(f"enabled = {'true' if waf.enabled else 'false'}")
        parts.append(f'mode = "{waf.mode}"')
        parts.append(f"paranoia = {waf.paranoia}")
        parts.append(f"inbound_threshold = {waf.inbound_threshold}")
        parts.append(f'body_limit = "{waf.body_limit}"')
        parts.append(f"inspect_responses = {'true' if waf.inspect_responses else 'false'}")
        parts.append(f"exclusions = {_toml_string_array(waf.exclusions)}")
        parts.append(f"exempt_paths = {_toml_string_array(waf.exempt_paths)}")
        parts.append("")
        # Nested table-arrays must follow the parent table's scalar keys.
        for rule in waf.custom_rules:
            parts.append("[[waf.custom_rules]]")
            parts.append(f"name = {_toml_string(rule.name)}")
            parts.append(f'action = "{rule.action}"')
            parts.append(f'match = "{rule.match}"')
            parts.append(f"enabled = {'true' if rule.enabled else 'false'}")
            for cond in rule.conditions:
                parts.append("[[waf.custom_rules.conditions]]")
                parts.append(f'field = "{cond.field}"')
                parts.append(f'operator = "{cond.operator}"')
                parts.append(f"value = {_toml_string(cond.value)}")
                parts.append(f"header_name = {_toml_string(cond.header_name)}")
            parts.append("")

    s3 = config.s3
    if s3.access_key or s3.secret_key or s3.bucket or s3.provider or s3.region:
        parts.append("[s3]")
        parts.append(f'access_key = "{s3.access_key}"')
        parts.append(f'secret_key = "{s3.secret_key}"')
        parts.append(f'bucket = "{s3.bucket}"')
        parts.append(f'provider = "{s3.provider}"')
        parts.append(f'region = "{s3.region}"')
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
