from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from pilot.config.toml_store import BenchTomlStore
from pilot.core.bench import Bench
from pilot.managers.cloudflare import CloudflareTunnelManager
from pilot.utils import encrypt, decrypt

cloudflare_bp = Blueprint("cloudflare", __name__)


def _get_login_session_file() -> Path:
    dir_path = Path.home() / ".cloudflared"
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / "login_session.json"


def _save_login_session(url: str, pid: int) -> None:
    session_file = _get_login_session_file()
    fd = os.open(str(session_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"url": url, "pid": pid, "created_at": time.time()}))


def _read_login_session() -> dict | None:
    session_file = _get_login_session_file()
    if not session_file.exists():
        return None
    try:
        return json.loads(session_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cancel_login_process() -> None:
    session = _read_login_session()
    session_file = _get_login_session_file()
    if session and "pid" in session:
        pid = session["pid"]
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if session_file.exists():
        try:
            session_file.unlink(missing_ok=True)
        except Exception:
            pass


def _start_login_process() -> str:
    _cancel_login_process()

    import shutil
    cloudflared_path = shutil.which("cloudflared") or str(Path.home() / ".local" / "bin" / "cloudflared")

    proc = subprocess.Popen(
        [cloudflared_path, "tunnel", "login"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    url_holder: list[str] = []

    def read_output():
        if proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if "https://" in line:
                    for word in line.split():
                        if word.startswith("https://"):
                            url_holder.append(word)
                            return

    t = threading.Thread(target=read_output)
    t.daemon = True
    t.start()

    start_time = time.time()
    while time.time() - start_time < 8.0:
        if url_holder:
            url = url_holder[0]
            _save_login_session(url, proc.pid)
            return url
        time.sleep(0.1)

    _cancel_login_process()
    raise RuntimeError("Failed to retrieve login URL from cloudflared.")


@cloudflare_bp.post("/login/start")
def start_cloudflare_login():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    store = BenchTomlStore.for_bench(bench_root)
    try:
        config = store.read()
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        manager.install()

        url = _start_login_process()
        return jsonify({"url": url})
    except Exception as e:
        return error_response("login_start_failed", f"Failed to start Cloudflare login: {e}", 500)


@cloudflare_bp.get("/login/status")
def get_cloudflare_login_status():
    cert_path = Path.home() / ".cloudflared" / "cert.pem"

    if cert_path.exists():
        _cancel_login_process()
        return jsonify({"status": "success", "cert_path": str(cert_path)})

    session = _read_login_session()
    if not session:
        return jsonify({"status": "idle"})

    pid = session.get("pid")
    url = session.get("url", "")

    is_alive = False
    if pid:
        try:
            os.kill(pid, 0)
            is_alive = True
        except (ProcessLookupError, PermissionError, OSError):
            is_alive = False

    if is_alive:
        return jsonify({"status": "pending", "url": url})

    time.sleep(0.5)
    if cert_path.exists():
        _cancel_login_process()
        return jsonify({"status": "success", "cert_path": str(cert_path)})

    _cancel_login_process()
    return jsonify({
        "status": "failed",
        "error": "Login process exited without creating credentials."
    })


@cloudflare_bp.post("/login/cancel")
def cancel_cloudflare_login():
    _cancel_login_process()
    return jsonify({"success": True})


@cloudflare_bp.post("/login/disconnect")
def disconnect_cloudflare_login():
    cert_path = Path.home() / ".cloudflared" / "cert.pem"
    try:
        if cert_path.exists():
            cert_path.unlink()
    except Exception as e:
        return error_response("disconnect_failed", f"Failed to remove certificate: {e}", 500)
    return jsonify({"success": True})


@cloudflare_bp.post("/action")
def perform_cloudflare_action():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("start", "stop", "restart"):
        return error_response("invalid_action", "Action must be start, stop, or restart.", 400)

    try:
        config = BenchTomlStore.for_bench(bench_root).read()
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        
        if action == "start":
            manager.start()
        elif action == "stop":
            manager.stop()
        elif action == "restart":
            manager.restart()
    except Exception as e:
        return error_response("action_failed", str(e), 500)

    return jsonify({"success": True})


@cloudflare_bp.post("/zones")
def get_cloudflare_zones():
    data = request.get_json(silent=True) or {}
    api_token = data.get("api_token")
    if not api_token:
        bench_root = Path(current_app.config["BENCH_ROOT"])
        try:
            config = BenchTomlStore.for_bench(bench_root).read()
            if config.cloudflare.api_token:
                api_token = decrypt(config.cloudflare.api_token)
        except Exception:
            pass

    if not api_token or api_token.startswith("*****"):
        return error_response("missing_token", "API Token is required.", 400)

    api_token = "".join(api_token.split())

    try:
        bench_root = Path(current_app.config["BENCH_ROOT"])
        config = BenchTomlStore.for_bench(bench_root).read()
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        
        res = manager._api_request("https://api.cloudflare.com/client/v4/zones?per_page=50", api_token)
        zones = [{"id": z["id"], "name": z["name"]} for z in res.get("result", [])]
        return jsonify({"zones": zones})
    except Exception as e:
        return error_response("fetch_failed", f"Failed to fetch zones: {e}", 500)


@cloudflare_bp.post("/create")
def provision_cloudflare_tunnel():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    
    api_token = data.get("api_token")
    domain_root = data.get("domain")
    subdomain = data.get("subdomain", "").strip()
    
    cert_path = Path.home() / ".cloudflared" / "cert.pem"
    has_cert = cert_path.exists()
    
    if not domain_root:
        return error_response("missing_parameters", "domain is required.", 400)
    if not has_cert and not api_token:
        return error_response("missing_parameters", "api_token is required.", 400)

    if api_token:
        api_token = "".join(api_token.split())
    domain_root = domain_root.strip()

    if api_token and (api_token.startswith("*****") or api_token.startswith("****")):
        try:
            store = BenchTomlStore.for_bench(bench_root)
            config = store.read()
            if config.cloudflare.api_token:
                api_token = decrypt(config.cloudflare.api_token)
        except Exception:
            pass

    if subdomain:
        hostname = f"{subdomain}.{domain_root}"
    else:
        hostname = domain_root

    import socket
    import re
    vm_hostname = socket.gethostname()
    tunnel_name = re.sub(r'[^a-zA-Z0-9-]', '-', vm_hostname)

    store = BenchTomlStore.for_bench(bench_root)
    try:
        config = store.read()
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        
        # Call Cloudflare API or CLI to provision tunnel
        if has_cert:
            tunnel_token, domain = manager.create_tunnel_via_cert(
                tunnel_name=tunnel_name,
                hostname=hostname
            )
            
            # For cert-based tunnels, extract tunnel_id and use local config
            import base64
            token_bytes = base64.b64decode(tunnel_token)
            token_data = json.loads(token_bytes.decode("utf-8"))
            tunnel_id = token_data["t"]
            
            # Save token & settings
            with store.edit() as config:
                config.cloudflare.enabled = True
                config.cloudflare.tunnel_name = tunnel_name
                config.cloudflare.domain = domain
                config.cloudflare.tunnel_token = encrypt(tunnel_token)
                config.cloudflare.api_token = ""

            # Use local config file for ingress (no API needed)
            config = store.read()
            bench = Bench(config, bench_root)
            manager = CloudflareTunnelManager(bench)
            manager.setup_service_with_config(
                tunnel_id=tunnel_id,
                tunnel_name=tunnel_name,
                hostname=domain,
                local_port=config.admin.internal_port
            )
            manager.start()
        else:
            tunnel_token, domain = manager.create_tunnel_via_api(
                api_token=api_token,
                zone_name=domain_root,
                tunnel_name=tunnel_name,
                hostname=hostname
            )
        
            # Configure ingress routing via Cloudflare API BEFORE setting up service
            manager.update_ingress_rule(
                hostname=domain,
                local_service=f"http://localhost:{config.admin.internal_port}"
            )

            # Save token & settings
            with store.edit() as config:
                config.cloudflare.enabled = True
                config.cloudflare.tunnel_name = tunnel_name
                config.cloudflare.domain = domain
                config.cloudflare.tunnel_token = encrypt(tunnel_token)
                config.cloudflare.api_token = encrypt(api_token)
                
            # Set up and start the background daemon service
            manager.setup_service(tunnel_token)
            manager.start()
        
    except Exception as e:
        # Clean up any partial service or configuration on failure
        try:
            manager.remove_service()
            with store.edit() as config:
                config.cloudflare.enabled = False
                config.cloudflare.tunnel_name = ""
                config.cloudflare.domain = ""
                config.cloudflare.tunnel_token = ""
                config.cloudflare.api_token = ""
        except Exception:
            pass
        return error_response("provisioning_failed", f"Failed to provision tunnel: {e}", 500)

    return jsonify({"success": True})


@cloudflare_bp.get("/sites/<name>")
def get_site_expose_status(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
    except Exception:
        return error_response("config_unavailable", "Could not read bench config.", 500)

    tunnel_configured = bool(config.cloudflare.tunnel_token)
    api_token_configured = bool(config.cloudflare.api_token)
    cert_configured = (Path.home() / ".cloudflared" / "cert.pem").exists()

    if not tunnel_configured or (not api_token_configured and not cert_configured):
        return jsonify({
            "exposed": False,
            "tunnel_configured": tunnel_configured,
            "api_token_configured": api_token_configured,
            "cert_configured": cert_configured
        })

    try:
        decrypted_token = decrypt(config.cloudflare.tunnel_token)
        decrypted_token = "".join(decrypted_token.split())
        padding = len(decrypted_token) % 4
        if padding:
            decrypted_token += "=" * (4 - padding)
        token_bytes = base64.b64decode(decrypted_token)
        token_data = json.loads(token_bytes.decode("utf-8"))
        
        account_id = token_data["a"]
        tunnel_id = token_data["t"]
        
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        admin_port = config.admin.internal_port
        exposed_domain = None

        if api_token_configured:
            # API-based: fetch ingress from Cloudflare API
            api_token = decrypt(config.cloudflare.api_token)
            api_token = "".join(api_token.split())
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
            res = manager._api_request(url, api_token)
            result = res.get("result") or {}
            config_data = result.get("config") or {}
            ingress = config_data.get("ingress") or []
            
            for rule in ingress:
                svc = rule.get("service", "")
                hostname = rule.get("hostname", "")
                if not hostname:
                    continue
                if ("localhost" in svc or "127.0.0.1" in svc) and f":{admin_port}" not in svc:
                    meta_site = rule.get("originRequest", {}).get("httpHostHeader", "")
                    if meta_site == name or hostname == name:
                        exposed_domain = hostname
                        break
                if rule.get("originRequest", {}).get("_pilot_site") == name:
                    exposed_domain = hostname
                    break
        else:
            # Cert-based: read ingress from local config file
            config_path = Path.home() / ".cloudflared" / f"{config.name}-config.yml"
            if config_path.exists():
                content = config_path.read_text(encoding="utf-8")
                import re
                for block in content.split("  - hostname:")[1:]:
                    hostname_match = re.search(r'^\s*(\S+)', block)
                    header_match = re.search(r'httpHostHeader:\s*(\S+)', block)
                    if hostname_match:
                        h = hostname_match.group(1).strip()
                        hdr = header_match.group(1).strip() if header_match else ""
                        if hdr == name or h == name:
                            exposed_domain = h
                            break
        
        return jsonify({
            "exposed": exposed_domain is not None,
            "domain": exposed_domain or "",
            "tunnel_configured": True,
            "api_token_configured": api_token_configured,
            "cert_configured": cert_configured
        })
    except Exception as e:
        return error_response("check_failed", f"Failed to fetch tunnel configuration: {e}", 500)


@cloudflare_bp.post("/sites/<name>/expose")
def toggle_site_expose(name: str):
    import re
    if not re.fullmatch(r"^[a-zA-Z0-9._-]+$", name):
        return error_response("invalid_site_name", "Invalid site name format.", 400)

    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    expose = bool(data.get("expose", False))
    public_domain = (data.get("domain") or "").strip().lower()
    
    if expose:
        if not public_domain:
            return error_response("missing_domain", "A public domain is required to expose a site via the tunnel.", 400)
        if not re.fullmatch(r"^[a-zA-Z0-9.-]+$", public_domain):
            return error_response("invalid_domain", "Invalid domain format.", 400)
    
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
    except Exception:
        return error_response("config_unavailable", "Could not read bench config.", 500)

    has_api_token = bool(config.cloudflare.api_token)
    has_tunnel_token = bool(config.cloudflare.tunnel_token)
    cert_path = Path.home() / ".cloudflared" / "cert.pem"
    has_cert = cert_path.exists()

    if not has_tunnel_token or (not has_api_token and not has_cert):
        return error_response("not_configured", "Cloudflare Tunnel must be configured first (via API Token or SSO certificate).", 400)

    try:
        decrypted_token = decrypt(config.cloudflare.tunnel_token)
        decrypted_token = "".join(decrypted_token.split())
        padding = len(decrypted_token) % 4
        if padding:
            decrypted_token += "=" * (4 - padding)
        token_bytes = base64.b64decode(decrypted_token)
        token_data = json.loads(token_bytes.decode("utf-8"))
        
        account_id = token_data["a"]
        tunnel_id = token_data["t"]
        tunnel_name = config.cloudflare.tunnel_name
        
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        
        hostname_to_use = public_domain if public_domain else name

        if has_api_token:
            # === API-based flow (unchanged) ===
            api_token = decrypt(config.cloudflare.api_token)
            api_token = "".join(api_token.split())
            
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
            res = manager._api_request(url, api_token)
            result = res.get("result") or {}
            config_data = result.get("config") or {}
            ingress = config_data.get("ingress") or []
            
            ingress = [r for r in ingress if r.get("hostname") not in (hostname_to_use, name)]
            
            segments = hostname_to_use.split(".")
            zone_name = ".".join(segments[-2:]) if len(segments) >= 2 else hostname_to_use
                
            zones_res = manager._api_request(f"https://api.cloudflare.com/client/v4/zones?name={zone_name}", api_token)
            if not zones_res.get("result"):
                return error_response("zone_not_found", f"Zone '{zone_name}' not found on Cloudflare.", 404)
            zone_id = zones_res["result"][0]["id"]
            
            if expose:
                new_rule = {
                    "hostname": hostname_to_use,
                    "service": "http://localhost:80",
                    "originRequest": {
                        "httpHostHeader": name,
                        "_pilot_site": name
                    }
                }
                if len(ingress) > 0:
                    ingress.insert(len(ingress) - 1, new_rule)
                else:
                    ingress.append(new_rule)
                    ingress.append({"service": "http_status:404"})
                    
                records_res = manager._api_request(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={hostname_to_use}&type=CNAME",
                    api_token
                )
                if not records_res.get("result"):
                    manager._api_request(
                        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                        api_token,
                        method="POST",
                        body={
                            "type": "CNAME",
                            "name": hostname_to_use,
                            "content": f"{tunnel_id}.cfargotunnel.com",
                            "ttl": 1,
                            "proxied": True
                        }
                    )
            else:
                records_res = manager._api_request(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={hostname_to_use}&type=CNAME",
                    api_token
                )
                if records_res.get("result"):
                    record_id = records_res["result"][0]["id"]
                    manager._api_request(
                        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}",
                        api_token,
                        method="DELETE"
                    )

            config_data["ingress"] = ingress
            manager._api_request(url, api_token, method="PUT", body={"config": config_data})
        else:
            # === Cert-based flow: update local config.yml + CLI DNS ===
            import re as _re
            import shutil

            config_file = Path.home() / ".cloudflared" / f"{config.name}-config.yml"
            if not config_file.exists():
                return error_response("config_missing", "Local tunnel config file not found.", 500)

            content = config_file.read_text(encoding="utf-8")

            if expose:
                # Insert new ingress entry before the catch-all rule (or append if sentinel missing)
                new_entry = f"  - hostname: {hostname_to_use}\n    service: http://localhost:80\n    originRequest:\n      httpHostHeader: {name}\n"
                if "  - service: http_status:404" in content:
                    content = content.replace(
                        "  - service: http_status:404",
                        new_entry + "  - service: http_status:404"
                    )
                else:
                    content = content.rstrip() + "\n" + new_entry + "  - service: http_status:404\n"
                config_file.write_text(content, encoding="utf-8")

                # Route DNS via CLI (non-fatal if already exists)
                cloudflared_path = shutil.which("cloudflared") or str(Path.home() / ".local" / "bin" / "cloudflared")
                dns_res = subprocess.run(
                    [cloudflared_path, "tunnel", "route", "dns", "--overwrite-dns", tunnel_name, hostname_to_use],
                    capture_output=True, text=True,
                )
                if dns_res.returncode != 0 and "already exists" not in f"{dns_res.stdout}\n{dns_res.stderr}".lower():
                    return error_response("dns_failed", f"Failed to create DNS record: {dns_res.stdout}\n{dns_res.stderr}", 500)
            else:
                # Remove the ingress entry for this hostname
                lines = content.split("\n")
                new_lines = []
                skip_block = False
                for line in lines:
                    if _re.match(r'\s*-\s*hostname:\s*' + _re.escape(hostname_to_use), line):
                        skip_block = True
                        continue
                    if skip_block:
                        if _re.match(r'\s*-\s', line) or (line.strip() == "" and not any(c.isalpha() for c in line)):
                            skip_block = False
                        else:
                            continue
                    new_lines.append(line)
                config_file.write_text("\n".join(new_lines), encoding="utf-8")

            # Restart the tunnel service to pick up config changes
            manager.restart()
        
    except Exception as e:
        return error_response("expose_failed", f"Failed to modify tunnel configuration: {e}", 500)

    return jsonify({"success": True})
