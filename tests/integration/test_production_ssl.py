"""
End-to-end test for the production deployment + SSL flow that powers the
snapshot-based onboarding:

    bench new <bench>
    edit bench.toml (dummy site) + bench init
    bench setup production --admin-domain bench.localhost --tls   # SSL site + admin
    bench rename-site site1.localhost <new-domain>                # new user clones snapshot
    bench setup production --admin-domain <new-admin-domain>      # refresh configs

This is the flow we must not break, so the test exercises it against a real
already-initialised bench (the same one the other integration tests use).

Let's Encrypt cannot validate ``*.localhost`` over the public internet, so
``needs_letsencrypt()`` returns False for these domains and certbot is never
invoked. We stand in for it by dropping **self-signed** certificates at the
exact paths nginx reads (``/etc/letsencrypt/live/<domain>/{fullchain,privkey}.pem``);
nginx then terminates TLS with them, letting us assert the full HTTPS chain
end to end.

The test installs system services and rewrites the machine's nginx config, so
it is destructive. It is gated behind BENCH_E2E_PRODUCTION=1 (set in CI) and
skips otherwise, and it tears everything down on the way out.

Prerequisites (CI provides these):
    - an initialised bench at BENCH_TEST_ROOT with site1.localhost
    - nginx, openssl, sudo available
"""

from __future__ import annotations

import os
import shutil
import subprocess
import json
from pathlib import Path

import pytest

SITE = "site1.localhost"
# A second site in the same (TLS-enabled) bench that is intentionally NOT
# SSL-enabled, so we cover a mixed bench: one https site + one http-only site.
SITE_NO_SSL = "site2.localhost"
RENAMED_SITE = "renamed.localhost"
ADMIN_DOMAIN = "bench.localhost"
ADMIN_DOMAIN_2 = "bench-admin2.localhost"
ADMIN_PASSWORD = "admin"
HTTP_PORT = 80
HTTPS_PORT = 443
# Marker baked into the self-signed certs so we can prove nginx served *ours*.
CERT_ORG = "bench-cli-e2e"
LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")

# All hostnames that get a self-signed cert / nginx vhost during the run.
ALL_DOMAINS = (SITE, RENAMED_SITE, ADMIN_DOMAIN, ADMIN_DOMAIN_2)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)


def _install_self_signed_cert(domain: str) -> None:
    """Issue a self-signed cert for *domain* and drop it where nginx expects a
    Let's Encrypt cert. /etc/letsencrypt/live is root-only, so we stage in /tmp
    and copy in with sudo."""
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
        return  # site not present in this bench — nothing to toggle
    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    data["ssl"] = enabled
    cfg.write_text(json.dumps(data, indent=1))


def _site_dir(bench_root: Path, site: str) -> Path:
    return bench_root / "sites" / site


def _https_status(domain: str) -> str:
    """HTTP status code nginx returns over TLS for *domain*, resolved to
    localhost. '000' means no/failed TLS response."""
    r = _run(
        "curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}",
        "--resolve", f"{domain}:{HTTPS_PORT}:127.0.0.1",
        f"https://{domain}/",
    )
    return r.stdout.strip()


def _request(domain: str, path: str, *, scheme: str = "https", method: str = "GET",
             json_body: dict | None = None) -> tuple[str, str]:
    """Make an HTTP(S) request to *domain* (resolved to localhost, self-signed
    cert accepted) and return (status_code, body). Used to prove nginx really
    proxies through to the workload / admin, not just terminates TLS."""
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


def _http_redirect(domain: str) -> tuple[str, str]:
    """Return (status_code, redirect_target) for a plain-HTTP request — an SSL
    site must 301 to its https:// URL."""
    r = _run(
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{redirect_url}",
        "--resolve", f"{domain}:{HTTP_PORT}:127.0.0.1", f"http://{domain}/",
    )
    code, _, target = r.stdout.strip().partition(" ")
    return code, target


def _set_admin_password(bench_root: Path, password: str) -> None:
    """Set admin.password in bench.toml so the admin reports full status and
    accepts logins. The admin re-reads bench.toml per request, so no restart."""
    import tomllib

    from bench_cli.utils import write_toml

    toml_path = bench_root / "bench.toml"
    data = tomllib.loads(toml_path.read_text())
    data.setdefault("admin", {})["password"] = password
    write_toml(toml_path, data)


def _set_admin_tls(bench_root: Path, enabled: bool) -> None:
    """Toggle admin.tls in bench.toml so setup production (without --tls) deploys
    the desired TLS mode."""
    import tomllib

    from bench_cli.utils import write_toml

    toml_path = bench_root / "bench.toml"
    data = tomllib.loads(toml_path.read_text())
    data.setdefault("admin", {})["tls"] = enabled
    write_toml(toml_path, data)


def _served_cert_org(domain: str) -> str:
    """Organisation field of the leaf cert nginx presents for *domain* — used to
    confirm it served our self-signed cert, not something else."""
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
    """Free the bench's redis ports so production's systemd-managed redis can
    bind them. CI pre-starts a plain daemonized redis on these ports for the
    non-production tests; left running it makes the systemd redis units
    crash-loop on a port clash and fail the whole deploy."""
    for port in _redis_ports(bench_root):
        _run("redis-cli", "-p", str(port), "shutdown", "nosave")


def _start_external_redis(bench_root: Path) -> None:
    """Restore a plain daemonized redis on the bench ports for tests that run
    after production is torn down (remove production stops the systemd redis)."""
    for conf in ("redis_cache.conf", "redis_queue.conf"):
        path = bench_root / "config" / conf
        if path.exists():
            _run("redis-server", str(path), "--daemonize", "yes")


# ---------------------------------------------------------------------------
# Module fixture: deploy production with self-signed SSL, tear it all down after
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def production(bench_root: Path, bench_bin: str):
    """Bring the bench up in production with TLS on site1.localhost + admin
    (backed by self-signed certs) and site2.localhost left HTTP-only, so one
    deploy covers a mixed bench. Yields the bench root, then removes the
    deployment, restores the site, and deletes the certs."""
    for domain in ALL_DOMAINS:
        _install_self_signed_cert(domain)
    _set_site_ssl(bench_root, SITE, True)
    # Second site stays HTTP-only inside the same TLS-enabled bench. No cert is
    # installed for it, so nginx must serve it over plain HTTP, not HTTPS.
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

    # Teardown — best effort, must leave the bench reusable for re-runs.
    current_site = RENAMED_SITE if _site_dir(bench_root, RENAMED_SITE).exists() else SITE
    _run(bench_bin, "remove", "production", cwd=bench_root)
    if current_site == RENAMED_SITE:
        _run(bench_bin, "rename-site", RENAMED_SITE, SITE, cwd=bench_root)
    _set_site_ssl(bench_root, SITE, False)
    for domain in ALL_DOMAINS:
        _remove_cert(domain)
    _start_external_redis(bench_root)


# ---------------------------------------------------------------------------
# Tests (run in definition order)
# ---------------------------------------------------------------------------

class TestProductionSSL:

    def test_bench_toml_records_production_state(self, production: Path) -> None:
        """setup production must persist enabled/domain/tls so the switcher and
        re-runs see a fully deployed bench."""
        import tomllib

        data = tomllib.loads((production / "bench.toml").read_text())
        assert data["production"]["enabled"] is True
        assert data["admin"]["domain"] == ADMIN_DOMAIN
        assert data["admin"]["tls"] is True
        assert data["admin"]["enabled"] is True

    def test_site_nginx_has_http_redirect_and_https_blocks(self, production: Path) -> None:
        """An SSL site gets two server blocks: an HTTP one that serves ACME
        challenges and 301-redirects everything else, and an HTTPS one with the
        cert, the proxy, and the realtime socket.io upstream."""
        conf = (_nginx_conf_dir(production) / "sites" / f"{SITE}.conf").read_text()

        # HTTP (:80) block — ACME passthrough + redirect to https.
        assert f"listen {HTTP_PORT};" in conf, conf
        assert "/.well-known/acme-challenge/" in conf
        assert "return 301 https://$host$request_uri;" in conf

        # HTTPS (:443) block — our self-signed cert + reverse proxy.
        assert f"listen {HTTPS_PORT} ssl" in conf, conf
        assert f"ssl_certificate     /etc/letsencrypt/live/{SITE}/fullchain.pem;" in conf
        assert f"ssl_certificate_key /etc/letsencrypt/live/{SITE}/privkey.pem;" in conf
        assert f"proxy_pass         http://bench-{_bench_name(production)};" in conf

    def test_admin_nginx_has_http_and_https_blocks(self, production: Path) -> None:
        """The admin domain gets the same treatment: HTTP redirects to HTTPS, and
        the HTTPS block terminates TLS with the admin cert and proxies the admin
        process."""
        conf = (_nginx_conf_dir(production) / "sites" / "_admin.conf").read_text()

        assert f"server_name {ADMIN_DOMAIN};" in conf, conf
        assert f"listen {HTTP_PORT};" in conf
        assert "return 301 https://$host$request_uri;" in conf

        assert f"listen {HTTPS_PORT} ssl" in conf
        assert f"ssl_certificate     /etc/letsencrypt/live/{ADMIN_DOMAIN}/fullchain.pem;" in conf
        assert f"ssl_certificate_key /etc/letsencrypt/live/{ADMIN_DOMAIN}/privkey.pem;" in conf

    def test_socketio_proxy_configured(self, production: Path) -> None:
        """Realtime needs nginx to proxy /socket.io and rewrite Origin to the
        external scheme/host, or HTTPS clients fail the realtime auth callback."""
        site_conf = (_nginx_conf_dir(production) / "sites" / f"{SITE}.conf").read_text()
        assert "location /socket.io {" in site_conf, site_conf
        assert "proxy_set_header   Origin $scheme://$http_host;" in site_conf

    def test_nginx_config_is_valid(self, production: Path) -> None:
        """The whole machine-wide nginx config (including our vhosts) must pass
        `nginx -t`, or the reload during setup would have served stale config."""
        r = _run("sudo", "nginx", "-t")
        assert r.returncode == 0, f"nginx -t failed:\n{r.stderr}"

    def test_site_served_over_https(self, production: Path) -> None:
        """nginx terminates TLS for the site and proxies to the workload: a real
        HTTP status comes back over HTTPS (any code but the 000 connect-failure)."""
        status = _https_status(SITE)
        assert status and status != "000", f"no HTTPS response from {SITE} (got {status!r})"

    def test_site_presents_our_certificate(self, production: Path) -> None:
        """Confirm nginx served the self-signed cert we installed, not a default
        or another bench's cert."""
        subject = _served_cert_org(SITE)
        assert CERT_ORG in subject, f"unexpected cert subject for {SITE}: {subject!r}"
        assert SITE in subject

    def test_admin_served_over_https(self, production: Path) -> None:
        status = _https_status(ADMIN_DOMAIN)
        assert status and status != "000", f"no HTTPS response from admin (got {status!r})"

    def test_site_serves_frappe_over_https(self, production: Path) -> None:
        """The deployed site must answer a real Frappe request end to end:
        nginx → workload → frappe. frappe.ping is whitelisted for guests, so it
        works regardless of wizard state."""
        status, body = _request(SITE, "/api/method/frappe.ping")
        assert status == "200", f"frappe.ping returned {status}: {body!r}"
        assert "pong" in body, f"expected pong from frappe, got: {body!r}"

    def test_site_redirects_http_to_https(self, production: Path) -> None:
        """An SSL site must bounce plain HTTP to its https:// URL."""
        code, target = _http_redirect(SITE)
        assert code in ("301", "308"), f"expected redirect, got {code}"
        assert target.startswith(f"https://{SITE}"), f"unexpected redirect target: {target!r}"

    def test_admin_status_endpoint_works(self, production: Path) -> None:
        """The admin must serve its open /api/status through nginx over HTTPS and
        report this bench as a live production deployment — proof the admin
        process is up and reachable, not just that nginx answered."""
        status, body = _request(ADMIN_DOMAIN, "/api/status")
        assert status == "200", f"/api/status returned {status}: {body!r}"
        data = json.loads(body)
        assert data.get("name"), f"admin status missing bench name: {data}"
        assert data.get("production") is True, f"admin does not report production: {data}"
        assert "native_process_manager" in data, data

    def test_admin_login_works(self, production: Path) -> None:
        """The admin accepts the configured password over HTTPS."""
        status, body = _request(
            ADMIN_DOMAIN, "/api/login", method="POST", json_body={"password": ADMIN_PASSWORD}
        )
        assert status == "200", f"/api/login returned {status}: {body!r}"
        assert json.loads(body).get("ok") is True, f"login not ok: {body!r}"

    # ── mixed bench: a second, non-SSL site alongside the https one ──────────

    def test_plain_site_vhost_has_no_ssl(self, production: Path) -> None:
        """The non-SSL site shares the TLS-enabled bench but must get an
        HTTP-only vhost: no ssl listener, no cert, no https redirect."""
        if not _site_dir(production, SITE_NO_SSL).is_dir():
            pytest.skip(f"{SITE_NO_SSL} not present in this bench")
        conf = (_nginx_conf_dir(production) / "sites" / f"{SITE_NO_SSL}.conf").read_text()
        assert "ssl_certificate" not in conf, f"plain site got SSL config:\n{conf}"
        assert f"listen {HTTPS_PORT} ssl" not in conf, conf
        assert "return 301 https" not in conf, conf

    def test_plain_site_served_over_http(self, production: Path) -> None:
        """The non-SSL site answers a real Frappe request over plain HTTP."""
        if not _site_dir(production, SITE_NO_SSL).is_dir():
            pytest.skip(f"{SITE_NO_SSL} not present in this bench")
        status, body = _request(SITE_NO_SSL, "/api/method/frappe.ping", scheme="http")
        assert status == "200", f"http frappe.ping returned {status}: {body!r}"
        assert "pong" in body, f"plain site not serving frappe: {body!r}"

    def test_plain_site_not_redirected_to_https(self, production: Path) -> None:
        """HTTP on the non-SSL site is served directly, not bounced to https."""
        if not _site_dir(production, SITE_NO_SSL).is_dir():
            pytest.skip(f"{SITE_NO_SSL} not present in this bench")
        code, target = _http_redirect(SITE_NO_SSL)
        assert code not in ("301", "308"), f"plain site unexpectedly redirected ({code} -> {target})"

    # ── resilience / idempotency ─────────────────────────────────────────────

    def test_unknown_host_handshake_rejected(self, production: Path) -> None:
        """The catch-all default server must reject TLS for hosts with no vhost
        (ssl_reject_handshake), so an unconfigured name can't be served another
        bench's cert. curl reports 000 when the handshake is dropped."""
        status = _https_status("definitely-not-configured.localhost")
        assert status == "000", f"unknown host was served over TLS (got {status!r})"

    def test_setup_production_idempotent(self, production: Path, bench_bin: str) -> None:
        """Re-running setup production with the same args must succeed and leave
        the deployment intact (operators re-run it routinely)."""
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
        # Site still serves after the re-run.
        status, _ = _request(SITE, "/api/method/frappe.ping")
        assert status == "200", f"site broke after idempotent re-run (got {status})"

    def test_rename_site_refreshes_production(self, production: Path, bench_bin: str) -> None:
        """rename-site moves the dummy site to a new hostname and, because the
        bench is in production, re-runs setup production for the new domain. The
        old vhost must be gone and the new one served over HTTPS."""
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
        """Re-running setup production with a new --admin-domain (the second half
        of the new-user flow) updates the deployment in place."""
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
        """`bench remove production` (run LAST) must tear the deployment down:
        flip production.enabled off in bench.toml and drop the bench's nginx
        vhost. Certs and the admin domain are intentionally kept for redeploy."""
        import tomllib

        # The current site name depends on whether the rename test ran.
        current_site = RENAMED_SITE if _site_dir(production, RENAMED_SITE).exists() else SITE

        r = _run(bench_bin, "remove", "production", cwd=production)
        assert r.returncode == 0, f"remove production failed:\n{r.stdout}\n{r.stderr}"

        data = tomllib.loads((production / "bench.toml").read_text())
        assert data["production"]["enabled"] is False, "production still enabled after remove"

        # The bench's shared-nginx vhost symlink must be gone.
        link = _bench_name(production) + ".conf"
        assert not (Path("/etc/nginx/conf.d") / link).exists(), "nginx vhost left behind"

        # Site no longer served over HTTPS once nginx is reloaded without it.
        assert _https_status(current_site) == "000", "site still served after remove"


# ---------------------------------------------------------------------------
# admin.tls = false: a central proxy terminates TLS, so the whole bench is
# served over plain HTTP and obtains no certificates.
# ---------------------------------------------------------------------------

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
    _set_admin_tls(bench_root, True)  # restore default for other classes
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
        """With admin.tls=false no admin cert exists, so the catch-all rejects an
        HTTPS handshake for the admin domain."""
        assert _https_status(ADMIN_DOMAIN) == "000", "admin answered HTTPS with TLS disabled"


# ---------------------------------------------------------------------------
# Process-manager migration: deploy on systemd, then switch to supervisord and
# confirm the old manager is torn down and the bench keeps serving.
# ---------------------------------------------------------------------------

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

        # New manager configured, old one torn down.
        assert (systemd_production / "config" / "supervisor" / "supervisord.conf").exists()
        assert not systemd_target.exists(), "systemd units left behind after migration"

        # The bench keeps serving across the switch.
        status, body = _request(SITE, "/api/method/frappe.ping")
        assert status == "200", f"site broke after PM migration ({status}): {body!r}"
        assert "pong" in body, body


# ---------------------------------------------------------------------------
# Multi-bench nginx sharing: a second bench's deploy must not break the first.
# Needs a second initialised bench; skipped unless BENCH_TEST_ROOT_2 is set.
# ---------------------------------------------------------------------------

class TestMultiBench:

    def test_second_bench_coexists(self, bench_root: Path, bench_bin: str) -> None:
        """Two benches share one nginx; deploying the second must not break the
        first. Self-contained (no `production` fixture) so it skips cheaply when
        no second bench is provided — env var checked before any deploy."""
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
            # First bench must still serve after the second rewrote shared nginx.
            status, _ = _request(SITE, "/api/method/frappe.ping")
            assert status == "200", f"first bench broke after second deploy ({status})"
        finally:
            _run(bench_bin, "remove", "production", cwd=root2)
            _run(bench_bin, "remove", "production", cwd=root1)
            _set_site_ssl(root1, SITE, False)
            for d in (SITE, ADMIN_DOMAIN, admin2):
                _remove_cert(d)
