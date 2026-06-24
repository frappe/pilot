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
    - nginx, openssl, sudo, uv available
"""

from __future__ import annotations

import os
import shutil
import subprocess
import json
from pathlib import Path

import pytest

SITE = "site1.localhost"
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
    for tool in ("openssl", "nginx", "sudo", "uv"):
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


# ---------------------------------------------------------------------------
# Module fixture: deploy production with self-signed SSL, tear it all down after
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def production(bench_root: Path, bench_bin: str):
    """Bring the bench up in production with TLS on site1.localhost + admin,
    backed by self-signed certs. Yields the bench root, then removes the
    deployment, restores the site, and deletes the certs."""
    for domain in ALL_DOMAINS:
        _install_self_signed_cert(domain)
    _set_site_ssl(bench_root, SITE, True)

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

    def test_nginx_config_uses_self_signed_certs(self, production: Path) -> None:
        """The generated vhosts must point nginx at the cert paths we populated,
        for both the SSL site and the admin domain."""
        conf = _nginx_conf_dir(production)
        site_conf = (conf / "sites" / f"{SITE}.conf").read_text()
        admin_conf = (conf / "sites" / "_admin.conf").read_text()

        assert f"listen {HTTPS_PORT} ssl" in site_conf, site_conf
        assert f"/etc/letsencrypt/live/{SITE}/fullchain.pem" in site_conf
        assert f"/etc/letsencrypt/live/{ADMIN_DOMAIN}/fullchain.pem" in admin_conf

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
