from __future__ import annotations

import base64
import json
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


@cloudflare_bp.get("")
def get_cloudflare_status():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
    except Exception:
        return error_response("config_unavailable", "Could not read bench config.", 500)

    bench = Bench(config, bench_root)
    manager = CloudflareTunnelManager(bench)
    
    return jsonify({
        "status": manager.status(),
        "enabled": config.cloudflare.enabled,
        "tunnel_name": config.cloudflare.tunnel_name,
        "domain": config.cloudflare.domain,
        "token_configured": bool(config.cloudflare.tunnel_token),
        "api_token_configured": bool(config.cloudflare.api_token),
        "cert_configured": (Path.home() / ".cloudflared" / "cert.pem").exists()
    })


@cloudflare_bp.patch("")
def update_cloudflare_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)

    store = BenchTomlStore.for_bench(bench_root)
    try:
        with store.edit() as config:
            cf = config.cloudflare
            if "enabled" in data:
                cf.enabled = bool(data["enabled"])
            if "tunnel_name" in data:
                cf.tunnel_name = str(data["tunnel_name"]).strip()
            if "domain" in data:
                cf.domain = str(data["domain"]).strip()
            if "tunnel_token" in data:
                token = str(data["tunnel_token"]).strip()
                if token and not token.startswith("*****"):
                    cf.tunnel_token = encrypt(token)
            if "api_token" in data:
                api_token = str(data["api_token"]).strip()
                if api_token and not api_token.startswith("*****"):
                    cf.api_token = encrypt(api_token)
    except Exception as e:
        return error_response("update_failed", f"Failed to save settings: {e}", 500)

    # Apply configuration (enable/disable systemd service)
    try:
        config = store.read()
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        
        if config.cloudflare.enabled:
            if not config.cloudflare.tunnel_token:
                return error_response("missing_token", "Cannot enable tunnel without a token.", 400)
            
            decrypted_token = decrypt(config.cloudflare.tunnel_token)
            manager.setup_service(decrypted_token)
            manager.start()
            
            if config.cloudflare.domain:
                manager.update_ingress_rule(
                    hostname=config.cloudflare.domain,
                    local_service=f"http://localhost:{config.admin.internal_port}"
                )
        else:
            manager.stop()
    except Exception as e:
        return error_response("apply_failed", f"Settings saved, but failed to apply to service: {e}", 500)


    return jsonify({"success": True})


@cloudflare_bp.delete("")
def delete_cloudflare_tunnel():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    store = BenchTomlStore.for_bench(bench_root)
    try:
        config = store.read()
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        
        # 1. Stop and remove systemd service
        manager.remove_service()
        
        # 2. Attempt to delete from Cloudflare if API token is configured
        if config.cloudflare.api_token and config.cloudflare.tunnel_token:
            try:
                import base64
                import json
                
                api_token = decrypt(config.cloudflare.api_token)
                api_token = "".join(api_token.split())
                
                decrypted_token = decrypt(config.cloudflare.tunnel_token)
                decrypted_token = "".join(decrypted_token.split())
                padding = len(decrypted_token) % 4
                if padding:
                    decrypted_token += "=" * (4 - padding)
                token_bytes = base64.b64decode(decrypted_token)
                token_data = json.loads(token_bytes.decode("utf-8"))
                
                account_id = token_data["a"]
                tunnel_id = token_data["t"]
                
                # Delete tunnel from Cloudflare
                manager._api_request(
                    f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}",
                    api_token,
                    method="DELETE"
                )
            except Exception:
                pass
        
        # 3. Clear configuration in bench.toml
        with store.edit() as config:
            config.cloudflare.enabled = False
            config.cloudflare.tunnel_name = ""
            config.cloudflare.domain = ""
            config.cloudflare.tunnel_token = ""
            config.cloudflare.api_token = ""
            
    except Exception as e:
        return error_response("delete_failed", f"Failed to delete tunnel configuration: {e}", 500)
    
    return jsonify({"success": True})


_login_processes: dict[str, subprocess.Popen] = {}
_login_urls: dict[str, str] = {}


def _start_login_process(bench_root: Path):
    global _login_processes, _login_urls
    
    _cancel_login_process(bench_root)
    
    cloudflared_path = "/home/frappe/.local/bin/cloudflared"
    
    proc = subprocess.Popen(
        [cloudflared_path, "tunnel", "login"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        close_fds=True
    )
    
    _login_processes[str(bench_root)] = proc
    _login_urls[str(bench_root)] = ""
    
    def read_output():
        url_found = False
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if "https://" in line:
                for word in line.split():
                    if word.startswith("https://"):
                        _login_urls[str(bench_root)] = word
                        url_found = True
                        break
            if url_found:
                break
                
    t = threading.Thread(target=read_output)
    t.daemon = True
    t.start()
    
    start_time = time.time()
    while time.time() - start_time < 5.0:
        if _login_urls[str(bench_root)]:
            return _login_urls[str(bench_root)]
        time.sleep(0.1)
        
    raise RuntimeError("Failed to retrieve login URL from cloudflared.")


def _cancel_login_process(bench_root: Path):
    global _login_processes, _login_urls
    key = str(bench_root)
    proc = _login_processes.get(key)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _login_processes.pop(key, None)
    _login_urls.pop(key, None)


@cloudflare_bp.post("/login/start")
def start_cloudflare_login():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    store = BenchTomlStore.for_bench(bench_root)
    try:
        config = store.read()
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        manager.install()
        
        url = _start_login_process(bench_root)
        return jsonify({"url": url})
    except Exception as e:
        return error_response("login_start_failed", f"Failed to start Cloudflare login: {e}", 500)


@cloudflare_bp.get("/login/status")
def get_cloudflare_login_status():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    cert_path = Path.home() / ".cloudflared" / "cert.pem"
    
    if cert_path.exists():
        _cancel_login_process(bench_root)
        return jsonify({"status": "success", "cert_path": str(cert_path)})
        
    key = str(bench_root)
    proc = _login_processes.get(key)
    if proc:
        exit_code = proc.poll()
        if exit_code is not None:
            time.sleep(0.5)
            if cert_path.exists() or exit_code == 0:
                _cancel_login_process(bench_root)
                return jsonify({"status": "success", "cert_path": str(cert_path)})
                
            _login_processes.pop(key, None)
            _login_urls.pop(key, None)
            return jsonify({
                "status": "failed",
                "error": f"Login process exited prematurely with code {exit_code}."
            })
            
        url = _login_urls.get(key, "")
        return jsonify({"status": "pending", "url": url})
        
    return jsonify({"status": "idle"})


@cloudflare_bp.post("/login/cancel")
def cancel_cloudflare_login():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    _cancel_login_process(bench_root)
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


@cloudflare_bp.get("/zones")
def get_cloudflare_zones():
    api_token = request.args.get("api_token")
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
        
            # Save token & settings
            with store.edit() as config:
                config.cloudflare.enabled = True
                config.cloudflare.tunnel_name = tunnel_name
                config.cloudflare.domain = domain
                config.cloudflare.tunnel_token = encrypt(tunnel_token)
                config.cloudflare.api_token = encrypt(api_token)
                
            # Start the service with token
            manager.setup_service(tunnel_token)
            manager.start()
            
            # Configure routing via Cloudflare API
            config = store.read()
            bench = Bench(config, bench_root)
            manager = CloudflareTunnelManager(bench)
            manager.update_ingress_rule(
                hostname=domain,
                local_service=f"http://localhost:{config.admin.internal_port}"
            )
        
    except Exception as e:
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
            config_path = Path.home() / ".cloudflared" / f"{config.bench.name}-config.yml"
            if config_path.exists():
                content = config_path.read_text(encoding="utf-8")
                # Simple parse: look for hostname lines
                import re
                for m in re.finditer(r'hostname:\s*(\S+)', content):
                    h = m.group(1)
                    if h == name:
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
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    expose = bool(data.get("expose", False))
    # Public domain provided by the user (e.g. test.codenetic.online)
    public_domain = (data.get("domain") or "").strip().lower()
    
    if expose and not public_domain:
        return error_response("missing_domain", "A public domain is required to expose a site via the tunnel.", 400)
    
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
    except Exception:
        return error_response("config_unavailable", "Could not read bench config.", 500)

    if not config.cloudflare.api_token or not config.cloudflare.tunnel_token:
        return error_response("not_configured", "Cloudflare API Token and Tunnel Token must be configured in Pilot first.", 400)

    try:
        import base64
        import json
        
        decrypted_token = decrypt(config.cloudflare.tunnel_token)
        decrypted_token = "".join(decrypted_token.split())
        padding = len(decrypted_token) % 4
        if padding:
            decrypted_token += "=" * (4 - padding)
        token_bytes = base64.b64decode(decrypted_token)
        token_data = json.loads(token_bytes.decode("utf-8"))
        
        account_id = token_data["a"]
        tunnel_id = token_data["t"]
        api_token = decrypt(config.cloudflare.api_token)
        api_token = "".join(api_token.split())
        
        bench = Bench(config, bench_root)
        manager = CloudflareTunnelManager(bench)
        
        # 1. Fetch current ingress configuration
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
        res = manager._api_request(url, api_token)
        result = res.get("result") or {}
        config_data = result.get("config") or {}
        ingress = config_data.get("ingress") or []
        
        # The ingress hostname to add/remove is the public domain, not the internal site name.
        # When removing, fall back to the public_domain provided by the caller.
        hostname_to_use = public_domain if public_domain else name
        
        # Filter out any existing rule for this public domain or internal site name
        ingress = [r for r in ingress if r.get("hostname") not in (hostname_to_use, name)]
        
        # 2. Extract Zone ID from the public domain
        target_for_zone = hostname_to_use if hostname_to_use else name
        segments = target_for_zone.split(".")
        if len(segments) >= 2:
            zone_name = ".".join(segments[-2:])
        else:
            zone_name = target_for_zone
            
        zones_res = manager._api_request(f"https://api.cloudflare.com/client/v4/zones?name={zone_name}", api_token)
        if not zones_res.get("result"):
            return error_response("zone_not_found", f"Zone '{zone_name}' not found on Cloudflare.", 404)
        zone_id = zones_res["result"][0]["id"]
        
        if expose:
            # Route through Nginx (port 80) so static assets are served correctly.
            # httpHostHeader rewrites the Host header to the internal site name so
            # Nginx knows which site vhost to serve.
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
                
            # Create/update DNS CNAME record for the public domain
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
            # Delete CNAME record for the public domain
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

        # 3. Push updated ingress rules back to Cloudflare
        config_data["ingress"] = ingress
        manager._api_request(
            url,
            api_token,
            method="PUT",
            body={"config": config_data}
        )
        
    except Exception as e:
        return error_response("expose_failed", f"Failed to modify tunnel configuration: {e}", 500)

    return jsonify({"success": True})
