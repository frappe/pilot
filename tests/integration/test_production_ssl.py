"""
End-to-end test for the production deployment + SSL flow:

    bench setup production --admin-domain bench.localhost --tls
    bench rename-site site1.localhost <new-domain>
    bench setup production --admin-domain <new-admin-domain>

Let's Encrypt can't validate ``*.localhost``, so certbot is never invoked;
we drop self-signed certs at the paths nginx reads
(``/etc/letsencrypt/live/<domain>/``) and assert the full HTTPS chain.

Destructive (installs services, rewrites nginx): gated behind
BENCH_E2E_PRODUCTION=1, skipped otherwise, torn down on the way out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SITE = "site1.localhost"
SITE_NO_SSL = "site2.localhost"
RENAMED_SITE = "renamed.localhost"
ADMIN_DOMAIN = "bench.localhost"
ADMIN_DOMAIN_2 = "bench-admin2.localhost"
ADMIN_PASSWORD = "admin"
HTTP_PORT = 80
HTTPS_PORT = 443
CERT_ORG = "bench-cli-e2e"  # baked into our certs to prove nginx served ours
LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
ALL_DOMAINS = (SITE, RENAMED_SITE, ADMIN_DOMAIN, ADMIN_DOMAIN_2)

pytestmark = [pytest.mark.integration, pytest.mark.production]


def _missing_tooling() -> str | None:
    if os.environ.get("BENCH_E2E_PRODUCTION") != "1":
        return "BENCH_E2E_PRODUCTION != 1 (destructive: installs services, rewrites nginx)"
    for tool in ("openssl", "nginx", "sudo"):
        if shutil.which(tool) is None:
            return f"required tool '{tool}' not on PATH"
    return None


@pytest.fixture(scope="module", autouse=True)
def _require_tooling():
    reason = _missing_tooling()
    if reason:
        pytest.skip(reason)


# ── helpers ──────────────────────────────────────────────────────────────

def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)


def _install_self_signed_cert(domain: str) -> None:
    # /etc/letsencrypt/live is root-only, so stage in /tmp and copy in with sudo.
    stage = Path(f"/tmp/e2e-cert-{domain}")
    stage.mkdir(parents=True, exist_ok=True)
    key = stage / "privkey.pem"
    crt = stage / "fullchain.pem"
    r = _run(
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(crt),
        "-days", "30", "-subj", f"/O={CERT_ORG}/CN={domain}",
        "-addext", f"subjectAltName=DNS:{domain}",
    )
    assert r.returncode == 0, f"openssl failed for {domain}: {r.stderr}"

    live = LETSENCRYPT_LIVE / domain
    assert _run("sudo", "mkdir", "-p", str(live)).returncode == 0
    for f in (key, crt):
        assert _run("sudo", "cp", str(f), str(live / f.name)).returncode == 0
    shutil.rmtree(stage, ignore_errors=True)


def _remove_cert(domain: str) -> None:
    _run("sudo", "rm", "-rf", str(LETSENCRYPT_LIVE / domain))


def _set_site_ssl(bench_root: Path, site: str, enabled: bool) -> None:
    cfg = bench_root / "sites" / site / "site_config.json"
    if not cfg.parent.is_dir():
        return
    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    data["ssl"] = enabled
    cfg.write_text(json.dumps(data, indent=1))


def _site_dir(bench_root: Path, site: str) -> Path:
    return bench_root / "sites" / site


def _https_status(domain: str) -> str:
    """HTTP status nginx returns over TLS for *domain*; '000' = no/failed TLS."""
    r = _run(
        "curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}",
        "--resolve", f"{domain}:{HTTPS_PORT}:127.0.0.1",
        f"https://{domain}/",
    )
    return r.stdout.strip()


def _request(domain: str, path: str, *, scheme: str = "https", method: str = "GET",
             json_body: dict | None = None) -> tuple[str, str]:
    port = HTTPS_PORT if scheme == "https" else HTTP_PORT
    args = ["curl", "-s", "-w", "\n%{http_code}",
            "--resolve", f"{domain}:{port}:127.0.0.1"]
    if scheme == "https":
        args.append("-k")
    if method != "GET":
        args += ["-X", method]
    if json_body is not None:
        args += ["-H", "Content-Type: application/json", "-d", json.dumps(json_body)]
    args.append(f"{scheme}://{domain}:{port}{path}")
    body, _, code = _run(*args).stdout.rpartition("\n")
    return code.strip(), body


def _request_ok(domain: str, path: str, *, tries: int = 20, delay: float = 0.5,
                **kwargs) -> tuple[str, str]:
    """Poll _request until it returns 200, to ride out the brief window where the
    workload is restarting (e.g. just after a process-manager migration)."""
    status, body = "000", ""
    for _ in range(tries):
        status, body = _request(domain, path, **kwargs)
        if status == "200":
            break
        time.sleep(delay)
    return status, body


def _http_redirect(domain: str) -> tuple[str, str]:
    r = _run(
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{redirect_url}",
        "--resolve", f"{domain}:{HTTP_PORT}:127.0.0.1", f"http://{domain}/",
    )
    code, _, target = r.stdout.strip().partition(" ")
    return code, target


def _set_admin_password(bench_root: Path, password: str) -> None:
    import tomllib

    from pilot.utils import write_toml

    toml_path = bench_root / "bench.toml"
    data = tomllib.loads(toml_path.read_text())
    data.setdefault("admin", {})["password"] = password
    write_toml(toml_path, data)


def _set_admin_tls(bench_root: Path, enabled: bool) -> None:
    import tomllib

    from pilot.utils import write_toml

    toml_path = bench_root / "bench.toml"
    data = tomllib.loads(toml_path.read_text())
    data.setdefault("admin", {})["tls"] = enabled
    write_toml(toml_path, data)


def _served_cert_org(domain: str) -> str:
    probe = subprocess.run(
        ["openssl", "s_client", "-connect", f"127.0.0.1:{HTTPS_PORT}",
         "-servername", domain],
        input="", capture_output=True, text=True,
    )
    subject = subprocess.run(
        ["openssl", "x509", "-noout", "-subject"],
        input=probe.stdout, capture_output=True, text=True,
    )
    return subject.stdout.strip()


def _nginx_conf_dir(bench_root: Path) -> Path:
    return bench_root / "config" / "nginx"


def _bench_name(bench_root: Path) -> str:
    import tomllib

    return tomllib.loads((bench_root / "bench.toml").read_text())["bench"]["name"]


def _redis_ports(bench_root: Path) -> list[int]:
    import tomllib

    r = tomllib.loads((bench_root / "bench.toml").read_text()).get("redis", {})
    return [p for p in (r.get("cache_port"), r.get("queue_port")) if p]


def _stop_external_redis(bench_root: Path) -> None:
    # Production manages its own redis; free the ports CI pre-started redis on,
    # or the managed units crash-loop on the clash and fail the deploy.
    for port in _redis_ports(bench_root):
        _run("redis-cli", "-p", str(port), "shutdown", "nosave")


def _start_external_redis(bench_root: Path) -> None:
    # Restore redis for tests that run after production (remove stops its redis).
    for conf in ("redis_cache.conf", "redis_queue.conf"):
        path = bench_root / "config" / conf
        if path.exists():
            _run("redis-server", str(path), "--daemonize", "yes")


# ── full TLS deploy: site1 https + site2 http-only + admin ───────────────

@pytest.fixture(scope="class")
def production(bench_root: Path, bench_bin: str):
    for domain in ALL_DOMAINS:
        _install_self_signed_cert(domain)
    _set_site_ssl(bench_root, SITE, True)
    _set_site_ssl(bench_root, SITE_NO_SSL, False)
    _stop_external_redis(bench_root)

    result = _run(
        bench_bin, "setup", "production",
        "--admin-domain", ADMIN_DOMAIN, "--tls",
        cwd=bench_root,
    )
    assert result.returncode == 0, (
        f"setup production failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    _set_admin_password(bench_root, ADMIN_PASSWORD)

    yield bench_root

    current_site = RENAMED_SITE if _site_dir(bench_root, RENAMED_SITE).exists() else SITE
    _run(bench_bin, "remove", "production", cwd=bench_root)
    if current_site == RENAMED_SITE:
        _run(bench_bin, "rename-site", RENAMED_SITE, SITE, cwd=bench_root)
    _set_site_ssl(bench_root, SITE, False)
    for domain in ALL_DOMAINS:
        _remove_cert(domain)
    _start_external_redis(bench_root)


class TestProductionSSL:

    def test_bench_toml_records_production_state(self, production: Path) -> None:
        import tomllib

        data = tomllib.loads((production / "bench.toml").read_text())
        assert data["production"]["enabled"] is True
        assert data["admin"]["domain"] == ADMIN_DOMAIN
        assert data["admin"]["tls"] is True
        assert data["admin"]["enabled"] is True

    def test_site_nginx_has_http_redirect_and_https_blocks(self, production: Path) -> None:
        conf = (_nginx_conf_dir(production) / "sites" / f"{SITE}.conf").read_text()

        assert f"listen {HTTP_PORT};" in conf, conf
        assert "/.well-known/acme-challenge/" in conf
        assert "return 301 https://$host$request_uri;" in conf

        assert f"listen {HTTPS_PORT} ssl" in conf, conf
        assert f"ssl_certificate     /etc/letsencrypt/live/{SITE}/fullchain.pem;" in conf
        assert f"ssl_certificate_key /etc/letsencrypt/live/{SITE}/privkey.pem;" in conf
        assert f"proxy_pass         http://bench-{_bench_name(production)};" in conf

    def test_admin_nginx_has_http_and_https_blocks(self, production: Path) -> None:
        conf = (_nginx_conf_dir(production) / "sites" / "_admin.conf").read_text()

        assert f"server_name {ADMIN_DOMAIN};" in conf, conf
        assert f"listen {HTTP_PORT};" in conf
        assert "return 301 https://$host$request_uri;" in conf

        assert f"listen {HTTPS_PORT} ssl" in conf
        assert f"ssl_certificate     /etc/letsencrypt/live/{ADMIN_DOMAIN}/fullchain.pem;" in conf
        assert f"ssl_certificate_key /etc/letsencrypt/live/{ADMIN_DOMAIN}/privkey.pem;" in conf

    def test_socketio_proxy_configured(self, production: Path) -> None:
        # Realtime auth fails over HTTPS unless nginx rewrites Origin to $scheme://$http_host.
        conf = (_nginx_conf_dir(production) / "sites" / f"{SITE}.conf").read_text()
        assert "location /socket.io {" in conf, conf
        assert "proxy_set_header   Origin $scheme://$http_host;" in conf

    def test_nginx_config_is_valid(self, production: Path) -> None:
        r = _run("sudo", "nginx", "-t")
        assert r.returncode == 0, f"nginx -t failed:\n{r.stderr}"

    def test_site_served_over_https(self, production: Path) -> None:
        status = _https_status(SITE)
        assert status and status != "000", f"no HTTPS response from {SITE} (got {status!r})"

    def test_site_presents_our_certificate(self, production: Path) -> None:
        subject = _served_cert_org(SITE)
        assert CERT_ORG in subject, f"unexpected cert subject for {SITE}: {subject!r}"
        assert SITE in subject

    def test_admin_served_over_https(self, production: Path) -> None:
        status = _https_status(ADMIN_DOMAIN)
        assert status and status != "000", f"no HTTPS response from admin (got {status!r})"

    def test_site_serves_frappe_over_https(self, production: Path) -> None:
        status, body = _request(SITE, "/api/method/frappe.ping")
        assert status == "200", f"frappe.ping returned {status}: {body!r}"
        assert "pong" in body, f"expected pong from frappe, got: {body!r}"

    def test_site_redirects_http_to_https(self, production: Path) -> None:
        code, target = _http_redirect(SITE)
        assert code in ("301", "308"), f"expected redirect, got {code}"
        assert target.startswith(f"https://{SITE}"), f"unexpected redirect target: {target!r}"

    def test_admin_status_endpoint_works(self, production: Path) -> None:
        status, body = _request(ADMIN_DOMAIN, "/api/status")
        assert status == "200", f"/api/status returned {status}: {body!r}"
        data = json.loads(body)
        assert data.get("name"), f"admin status missing bench name: {data}"
        assert data.get("production") is True, f"admin does not report production: {data}"
        assert "native_process_manager" in data, data

    def test_admin_login_works(self, production: Path) -> None:
        status, body = _request(
            ADMIN_DOMAIN, "/api/login", method="POST", json_body={"password": ADMIN_PASSWORD}
        )
        assert status == "200", f"/api/login returned {status}: {body!r}"
        assert json.loads(body).get("ok") is True, f"login not ok: {body!r}"

    def test_plain_site_vhost_has_no_ssl(self, production: Path) -> None:
        if not _site_dir(production, SITE_NO_SSL).is_dir():
            pytest.skip(f"{SITE_NO_SSL} not present in this bench")
        conf = (_nginx_conf_dir(production) / "sites" / f"{SITE_NO_SSL}.conf").read_text()
        assert "ssl_certificate" not in conf, f"plain site got SSL config:\n{conf}"
        assert f"listen {HTTPS_PORT} ssl" not in conf, conf
        assert "return 301 https" not in conf, conf

    def test_plain_site_served_over_http(self, production: Path) -> None:
        if not _site_dir(production, SITE_NO_SSL).is_dir():
            pytest.skip(f"{SITE_NO_SSL} not present in this bench")
        status, body = _request(SITE_NO_SSL, "/api/method/frappe.ping", scheme="http")
        assert status == "200", f"http frappe.ping returned {status}: {body!r}"
        assert "pong" in body, f"plain site not serving frappe: {body!r}"

    def test_plain_site_not_redirected_to_https(self, production: Path) -> None:
        if not _site_dir(production, SITE_NO_SSL).is_dir():
            pytest.skip(f"{SITE_NO_SSL} not present in this bench")
        code, target = _http_redirect(SITE_NO_SSL)
        assert code not in ("301", "308"), f"plain site unexpectedly redirected ({code} -> {target})"

    def test_unknown_host_handshake_rejected(self, production: Path) -> None:
        # Catch-all default server uses ssl_reject_handshake, so an unconfigured
        # host can't be served another bench's cert; curl reports 000.
        status = _https_status("definitely-not-configured.localhost")
        assert status == "000", f"unknown host was served over TLS (got {status!r})"

    def test_setup_production_idempotent(self, production: Path, bench_bin: str) -> None:
        import tomllib

        r = _run(
            bench_bin, "setup", "production",
            "--admin-domain", ADMIN_DOMAIN, "--tls",
            cwd=production,
        )
        assert r.returncode == 0, f"idempotent re-run failed:\n{r.stdout}\n{r.stderr}"
        data = tomllib.loads((production / "bench.toml").read_text())
        assert data["production"]["enabled"] is True
        assert data["admin"]["domain"] == ADMIN_DOMAIN
        status, _ = _request(SITE, "/api/method/frappe.ping")
        assert status == "200", f"site broke after idempotent re-run (got {status})"

    def test_rename_site_refreshes_production(self, production: Path, bench_bin: str) -> None:
        r = _run(bench_bin, "rename-site", SITE, RENAMED_SITE, cwd=production)
        assert r.returncode == 0, f"rename-site failed:\n{r.stdout}\n{r.stderr}"

        assert _site_dir(production, RENAMED_SITE).exists()
        assert not _site_dir(production, SITE).exists()

        sites_dir = _nginx_conf_dir(production) / "sites"
        assert not (sites_dir / f"{SITE}.conf").exists(), "stale site vhost not pruned"
        assert (sites_dir / f"{RENAMED_SITE}.conf").exists()

        status, body = _request(RENAMED_SITE, "/api/method/frappe.ping")
        assert status == "200", f"renamed site frappe.ping returned {status}: {body!r}"
        assert "pong" in body, f"renamed site not serving frappe: {body!r}"

    def test_setup_production_updates_admin_domain(self, production: Path, bench_bin: str) -> None:
        import tomllib

        r = _run(
            bench_bin, "setup", "production",
            "--admin-domain", ADMIN_DOMAIN_2, "--tls",
            cwd=production,
        )
        assert r.returncode == 0, f"re-setup failed:\n{r.stdout}\n{r.stderr}"

        data = tomllib.loads((production / "bench.toml").read_text())
        assert data["admin"]["domain"] == ADMIN_DOMAIN_2
        assert data["production"]["enabled"] is True

        admin_conf = (_nginx_conf_dir(production) / "sites" / "_admin.conf").read_text()
        assert ADMIN_DOMAIN_2 in admin_conf
        status, body = _request(ADMIN_DOMAIN_2, "/api/status")
        assert status == "200", f"new admin domain /api/status returned {status}: {body!r}"
        assert json.loads(body).get("production") is True, f"admin not live on new domain: {body!r}"

    def test_remove_production(self, production: Path, bench_bin: str) -> None:
        # Runs last; teardown's remove production is then a no-op.
        import tomllib

        current_site = RENAMED_SITE if _site_dir(production, RENAMED_SITE).exists() else SITE

        r = _run(bench_bin, "remove", "production", cwd=production)
        assert r.returncode == 0, f"remove production failed:\n{r.stdout}\n{r.stderr}"

        data = tomllib.loads((production / "bench.toml").read_text())
        assert data["production"]["enabled"] is False, "production still enabled after remove"

        link = _bench_name(production) + ".conf"
        assert not (Path("/etc/nginx/conf.d") / link).exists(), "nginx vhost left behind"

        # Vhost gone -> catch-all rejects (000) or no upstream (502); never 200.
        assert _https_status(current_site) != "200", "site still served after remove"


# ── admin.tls = false: central proxy terminates TLS, bench serves plain HTTP ──

@pytest.fixture(scope="class")
def http_only_production(bench_root: Path, bench_bin: str):
    _set_admin_tls(bench_root, False)
    _set_admin_password(bench_root, ADMIN_PASSWORD)
    _stop_external_redis(bench_root)

    result = _run(
        bench_bin, "setup", "production", "--admin-domain", ADMIN_DOMAIN,
        cwd=bench_root,  # no --tls: keeps admin.tls=false from bench.toml
    )
    assert result.returncode == 0, (
        f"http-only setup production failed:\n{result.stdout}\n{result.stderr}"
    )

    yield bench_root

    _run(bench_bin, "remove", "production", cwd=bench_root)
    _set_admin_tls(bench_root, True)
    _start_external_redis(bench_root)


class TestProductionNoTLS:

    def test_admin_vhost_is_http_only(self, http_only_production: Path) -> None:
        conf = (_nginx_conf_dir(http_only_production) / "sites" / "_admin.conf").read_text()
        assert f"server_name {ADMIN_DOMAIN};" in conf, conf
        assert f"listen {HTTP_PORT};" in conf
        assert "ssl_certificate" not in conf, f"admin got TLS while admin.tls=false:\n{conf}"
        assert "return 301 https" not in conf, conf

    def test_admin_served_over_http(self, http_only_production: Path) -> None:
        status, body = _request(ADMIN_DOMAIN, "/api/status", scheme="http")
        assert status == "200", f"admin /api/status over http returned {status}: {body!r}"
        assert json.loads(body).get("production") is True, body

    def test_admin_not_served_over_https(self, http_only_production: Path) -> None:
        assert _https_status(ADMIN_DOMAIN) == "000", "admin answered HTTPS with TLS disabled"


# ── process-manager migration: systemd -> supervisord ────────────────────

@pytest.fixture(scope="class")
def systemd_production(bench_root: Path, bench_bin: str):
    _install_self_signed_cert(SITE)
    _install_self_signed_cert(ADMIN_DOMAIN)
    _set_admin_tls(bench_root, True)
    _set_admin_password(bench_root, ADMIN_PASSWORD)
    _set_site_ssl(bench_root, SITE, True)
    _stop_external_redis(bench_root)

    result = _run(
        bench_bin, "setup", "production", "--process-manager", "systemd",
        "--admin-domain", ADMIN_DOMAIN, "--tls", cwd=bench_root,
    )
    assert result.returncode == 0, (
        f"systemd setup production failed:\n{result.stdout}\n{result.stderr}"
    )

    yield bench_root

    _run(bench_bin, "remove", "production", cwd=bench_root)
    _set_site_ssl(bench_root, SITE, False)
    _remove_cert(SITE)
    _remove_cert(ADMIN_DOMAIN)
    _start_external_redis(bench_root)


class TestProcessManagerMigration:

    def test_migrate_systemd_to_supervisord(self, systemd_production: Path, bench_bin: str) -> None:
        import tomllib

        name = _bench_name(systemd_production)
        systemd_target = Path.home() / ".config" / "systemd" / "user" / f"{name}.target"
        assert systemd_target.exists(), "systemd target missing before migration"

        r = _run(
            bench_bin, "setup", "production", "--process-manager", "supervisord",
            "--admin-domain", ADMIN_DOMAIN, "--tls", cwd=systemd_production,
        )
        assert r.returncode == 0, f"migration to supervisord failed:\n{r.stdout}\n{r.stderr}"

        data = tomllib.loads((systemd_production / "bench.toml").read_text())
        assert data["production"]["process_manager"] == "supervisor", data
        assert (systemd_production / "config" / "supervisor" / "supervisord.conf").exists()
        assert not systemd_target.exists(), "systemd units left behind after migration"

        status, body = _request_ok(SITE, "/api/method/frappe.ping")
        assert status == "200", f"site broke after PM migration ({status}): {body!r}"
        assert "pong" in body, body


# ── multi-bench nginx sharing (needs BENCH_TEST_ROOT_2) ──────────────────

class TestMultiBench:

    def test_second_bench_coexists(self, bench_root: Path, bench_bin: str) -> None:
        root2 = os.environ.get("BENCH_TEST_ROOT_2")
        if not root2:
            pytest.skip("set BENCH_TEST_ROOT_2 to a second initialised bench to test nginx sharing")
        root1 = bench_root
        root2 = Path(root2)
        admin2 = "bench2-admin.localhost"
        _install_self_signed_cert(SITE)
        _install_self_signed_cert(ADMIN_DOMAIN)
        _install_self_signed_cert(admin2)
        _set_site_ssl(root1, SITE, True)
        try:
            r1 = _run(bench_bin, "setup", "production", "--admin-domain", ADMIN_DOMAIN, "--tls", cwd=root1)
            assert r1.returncode == 0, f"first bench deploy failed:\n{r1.stdout}\n{r1.stderr}"
            r2 = _run(bench_bin, "setup", "production", "--admin-domain", admin2, "--tls", cwd=root2)
            assert r2.returncode == 0, f"second bench deploy failed:\n{r2.stdout}\n{r2.stderr}"
            status, _ = _request(SITE, "/api/method/frappe.ping")
            assert status == "200", f"first bench broke after second deploy ({status})"
        finally:
            _run(bench_bin, "remove", "production", cwd=root2)
            _run(bench_bin, "remove", "production", cwd=root1)
            _set_site_ssl(root1, SITE, False)
            for d in (SITE, ADMIN_DOMAIN, admin2):
                _remove_cert(d)
