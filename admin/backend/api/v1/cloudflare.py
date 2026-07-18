from __future__ import annotations

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
        "api_token_configured": bool(config.cloudflare.api_token)
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
    
    if not api_token or not domain_root:
        return error_response("missing_parameters", "api_token and domain are required.", 400)

    api_token = "".join(api_token.split())
    domain_root = domain_root.strip()

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
        
        # Call Cloudflare API to provision tunnel
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
            
        # Start the service
        manager.setup_service(tunnel_token)
        manager.start()
        
        # Configure routing for the admin domain
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

    if not tunnel_configured or not api_token_configured:
        return jsonify({
            "exposed": False,
            "tunnel_configured": tunnel_configured,
            "api_token_configured": api_token_configured
        })

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
        
        # Fetch current configuration from Cloudflare
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
        res = manager._api_request(url, api_token)
        result = res.get("result") or {}
        config_data = result.get("config") or {}
        ingress = config_data.get("ingress") or []
        
        # Find any ingress rule that routes to the local bench webserver
        # (anything pointing to localhost/127.0.0.1 but NOT the admin port)
        admin_port = config.admin.internal_port
        exposed_domain = None
        for rule in ingress:
            svc = rule.get("service", "")
            hostname = rule.get("hostname", "")
            if not hostname:
                continue
            # Check if this rule points to the bench web port (not admin)
            if ("localhost" in svc or "127.0.0.1" in svc) and f":{admin_port}" not in svc:
                # Match by site name stored in rule metadata or by checking site_config domains
                meta_site = rule.get("originRequest", {}).get("httpHostHeader", "")
                if meta_site == name or hostname == name:
                    exposed_domain = hostname
                    break
            # Also match if the hostname is explicitly tagged for this site
            if rule.get("originRequest", {}).get("_pilot_site") == name:
                exposed_domain = hostname
                break
        
        return jsonify({
            "exposed": exposed_domain is not None,
            "domain": exposed_domain or "",
            "tunnel_configured": True,
            "api_token_configured": True
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
