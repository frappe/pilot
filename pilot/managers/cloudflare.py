from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import urllib.request
import urllib.error
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.utils import run_command, decrypt
from pilot.exceptions import BenchError


if TYPE_CHECKING:
    from pilot.core.bench import Bench

_CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

class CloudflareTunnelManager:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    @property
    def service_name(self) -> str:
        return f"{self.bench.config.name}-tunnel.service"

    @property
    def service_path(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user" / self.service_name

    @staticmethod
    def is_installed() -> bool:
        if shutil.which("cloudflared"):
            return True
        return (Path.home() / ".local" / "bin" / "cloudflared").exists()

    def install(self) -> None:
        if self.is_installed():
            return

        # Download the binary to ~/.local/bin/cloudflared
        bin_dir = Path.home() / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        dest = bin_dir / "cloudflared"

        try:
            urllib.request.urlretrieve(_CLOUDFLARED_URL, dest)
            dest.chmod(0o755)
        except Exception as e:
            raise BenchError(f"Failed to download cloudflared: {e}")

    def setup_service(self, token: str) -> None:
        self.install()

        cloudflared_path = shutil.which("cloudflared")
        if not cloudflared_path:
            cloudflared_path = str(Path.home() / ".local" / "bin" / "cloudflared")

        self.service_path.parent.mkdir(parents=True, exist_ok=True)
        env_file = self.service_path.parent / f"{self.bench.config.name}-tunnel.env"
        env_file.write_text(f"TUNNEL_TOKEN={token}\n", encoding="utf-8")
        env_file.chmod(0o600)

        service_content = f"""[Unit]
Description=Frappe Pilot Cloudflare Tunnel ({self.bench.config.name})
After=network.target

[Service]
Type=simple
EnvironmentFile={env_file}
ExecStart={cloudflared_path} --no-autoupdate tunnel run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
        self.service_path.write_text(service_content, encoding="utf-8")
        
        # Reload systemd manager config
        self._systemctl("daemon-reload")

    def setup_service_with_config(self, tunnel_id: str, tunnel_name: str, hostname: str, local_port: int) -> None:
        """Set up tunnel service using a local config.yml (for cert/CLI-based tunnels)."""
        self.install()

        cloudflared_path = shutil.which("cloudflared")
        if not cloudflared_path:
            cloudflared_path = str(Path.home() / ".local" / "bin" / "cloudflared")

        # Write config.yml in ~/.cloudflared/
        config_dir = Path.home() / ".cloudflared"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{self.bench.config.name}-config.yml"

        creds_path = config_dir / f"{tunnel_id}.json"

        config_content = f"""tunnel: {tunnel_id}
credentials-file: {creds_path}
ingress:
  - hostname: {hostname}
    service: http://localhost:{local_port}
  - service: http_status:404
"""

        config_path.write_text(config_content, encoding="utf-8")

        service_content = f"""[Unit]
Description=Frappe Pilot Cloudflare Tunnel ({self.bench.config.name})
After=network.target

[Service]
Type=simple
ExecStart={cloudflared_path} --no-autoupdate tunnel --config {config_path} run {tunnel_name}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
        self.service_path.parent.mkdir(parents=True, exist_ok=True)
        self.service_path.write_text(service_content, encoding="utf-8")

        # Reload systemd manager config
        self._systemctl("daemon-reload")


    def start(self) -> None:
        self._systemctl("enable", self.service_name)
        self._systemctl("start", self.service_name)

    def stop(self) -> None:
        if self.service_path.exists():
            try:
                self._systemctl("stop", self.service_name)
                self._systemctl("disable", self.service_name)
            except Exception:
                pass

    def restart(self) -> None:
        if self.service_path.exists():
            self._systemctl("restart", self.service_name)

    def status(self) -> str:
        if not self.service_path.exists():
            return "Not Installed"
        
        env = dict(os.environ)
        if not env.get("XDG_RUNTIME_DIR"):
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
            
        import subprocess
        res = subprocess.run(
            ["systemctl", "--user", "is-active", self.service_name],
            capture_output=True,
            text=True,
            env=env
        )
        status = res.stdout.strip()
        if status == "active":
            return "Running"
        elif status == "inactive":
            return "Stopped"
        else:
            return "Failed"

    def remove_service(self) -> None:
        if self.service_path.exists():
            self.stop()
            self.service_path.unlink()
            env_file = self.service_path.parent / f"{self.bench.config.name}-tunnel.env"
            if env_file.exists():
                env_file.unlink()
            self._systemctl("daemon-reload")

    def _systemctl(self, *args: str) -> None:
        env = dict(os.environ)
        if not env.get("XDG_RUNTIME_DIR"):
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        run_command(["systemctl", "--user", *args], env=env)

    @staticmethod
    def _api_request(url: str, token: str, method: str = "GET", body: dict | None = None) -> dict:
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            
        try:
            with urllib.request.urlopen(req, data=data, timeout=15) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_msg)
                if "errors" in err_json and err_json["errors"]:
                    raise RuntimeError(err_json["errors"][0]["message"])
            except Exception:
                pass
            raise RuntimeError(f"Cloudflare API error: {e.code} - {e.reason} ({err_msg})")
        except Exception as e:
            raise RuntimeError(f"Network error calling Cloudflare: {e}")

    def create_tunnel_via_api(self, api_token: str, zone_name: str, tunnel_name: str, hostname: str) -> tuple[str, str]:
        """Creates a Cloudflare Tunnel, CNAME record, and returns (tunnel_token, domain)."""
        # 1. Fetch Account ID
        accounts_res = self._api_request("https://api.cloudflare.com/client/v4/accounts", api_token)
        if not accounts_res.get("result"):
            raise RuntimeError("No accounts found for the provided API token.")
        account_id = accounts_res["result"][0]["id"]

        # 2. Get Zone ID from Zone Name
        zones_res = self._api_request(f"https://api.cloudflare.com/client/v4/zones?name={zone_name}", api_token)
        if not zones_res.get("result"):
            raise RuntimeError(f"Zone '{zone_name}' not found on Cloudflare.")
        zone_id = zones_res["result"][0]["id"]

        # 3. Create Tunnel
        tunnel_secret = secrets.token_bytes(32)
        tunnel_secret_b64 = base64.b64encode(tunnel_secret).decode("utf-8")
        
        create_res = self._api_request(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/tunnels",
            api_token,
            method="POST",
            body={
                "name": tunnel_name,
                "config_src": "cloudflare",
                "tunnel_secret": tunnel_secret_b64
            }
        )
        tunnel_id = create_res["result"]["id"]

        # 4. Generate CNAME record mapping host to <tunnel_id>.cfargotunnel.com
        records_res = self._api_request(
            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={hostname}",
            api_token
        )
        if records_res.get("result"):
            record_id = records_res["result"][0]["id"]
            self._api_request(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}",
                api_token,
                method="PUT",
                body={
                    "type": "CNAME",
                    "name": hostname,
                    "content": f"{tunnel_id}.cfargotunnel.com",
                    "ttl": 1,
                    "proxied": True
                }
            )
        else:
            self._api_request(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                api_token,
                method="POST",
                body={
                    "type": "CNAME",
                    "name": hostname,
                    "content": f"{tunnel_id}.cfargotunnel.com",
                    "ttl": 1,
                    "proxied": True
                }
            )

        # 5. Generate Tunnel Token
        token_data = {
            "a": account_id,
            "t": tunnel_id,
            "s": tunnel_secret_b64
        }
        tunnel_token = base64.b64encode(json.dumps(token_data).encode("utf-8")).decode("utf-8")

        return tunnel_token, hostname

    def update_ingress_rule(self, hostname: str, local_service: str) -> None:
        """Configures or updates a route for hostname pointing to local_service,
        and creates the DNS CNAME record if it doesn't exist."""
        if not self.bench.config.cloudflare.api_token or not self.bench.config.cloudflare.tunnel_token:
            return

        api_token = decrypt(self.bench.config.cloudflare.api_token)
        api_token = "".join(api_token.split())

        decrypted_token = decrypt(self.bench.config.cloudflare.tunnel_token)
        decrypted_token = "".join(decrypted_token.split())
        padding = len(decrypted_token) % 4
        if padding:
            decrypted_token += "=" * (4 - padding)
        token_bytes = base64.b64decode(decrypted_token)
        token_data = json.loads(token_bytes.decode("utf-8"))
        
        account_id = token_data["a"]
        tunnel_id = token_data["t"]

        # 1. Fetch current ingress configuration
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
        res = self._api_request(url, api_token)
        result = res.get("result") or {}
        config_data = result.get("config") or {}
        ingress = config_data.get("ingress") or []

        # Filter out existing rule for this hostname if it exists
        ingress = [r for r in ingress if r.get("hostname") != hostname]

        # Add new rule
        new_rule = {"hostname": hostname, "service": local_service}
        if len(ingress) > 0:
            # Insert before the last catch-all rule if it exists
            if ingress[-1].get("service", "").startswith("http_status:"):
                ingress.insert(len(ingress) - 1, new_rule)
            else:
                ingress.append(new_rule)
        else:
            ingress.append(new_rule)
            ingress.append({"service": "http_status:404"})

        # 2. Push updated ingress rules back to Cloudflare
        config_data["ingress"] = ingress
        self._api_request(
            url,
            api_token,
            method="PUT",
            body={"config": config_data}
        )

        # 3. Create CNAME record if missing
        segments = hostname.split(".")
        if len(segments) >= 2:
            zone_name = ".".join(segments[-2:])
        else:
            zone_name = hostname

        zones_res = self._api_request(f"https://api.cloudflare.com/client/v4/zones?name={zone_name}", api_token)
        if zones_res.get("result"):
            zone_id = zones_res["result"][0]["id"]
            records_res = self._api_request(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={hostname}&type=CNAME",
                api_token
            )
            if not records_res.get("result"):
                self._api_request(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                    api_token,
                    method="POST",
                    body={
                        "type": "CNAME",
                        "name": hostname,
                        "content": f"{tunnel_id}.cfargotunnel.com",
                        "ttl": 1,
                        "proxied": True
                    }
                )

    def create_tunnel_via_cert(self, tunnel_name: str, hostname: str) -> tuple[str, str]:
        """Creates a Cloudflare Tunnel using cert.pem and returns (tunnel_token, domain)."""
        import subprocess
        import re
        import logging

        logger = logging.getLogger(__name__)

        cloudflared_path = shutil.which("cloudflared")
        if not cloudflared_path:
            cloudflared_path = str(Path.home() / ".local" / "bin" / "cloudflared")

        # Step 1: Create tunnel (or reuse existing one with the same name)
        res = subprocess.run(
            [cloudflared_path, "tunnel", "create", tunnel_name],
            capture_output=True,
            text=True,
        )

        tunnel_id = None
        combined = f"{res.stdout}\n{res.stderr}"

        if res.returncode == 0:
            match = re.search(r"Created tunnel [^\s]+ with id ([a-f0-9-]+)", combined)
            if match:
                tunnel_id = match.group(1)
        
        # If creation failed because the tunnel already exists, look it up
        if tunnel_id is None:
            if "already exists" in combined.lower():
                logger.info(f"Tunnel '{tunnel_name}' already exists, reusing it.")
            list_res = subprocess.run(
                [cloudflared_path, "tunnel", "list", "-o", "json"],
                capture_output=True,
                text=True,
            )
            if list_res.returncode == 0 and list_res.stdout.strip():
                tunnels = json.loads(list_res.stdout)
                for t in tunnels:
                    if t.get("name") == tunnel_name:
                        tunnel_id = t["id"]
                        break

        if not tunnel_id:
            raise RuntimeError(f"Failed to create or find tunnel '{tunnel_name}': {combined}")

        # Step 2: Read credentials file
        creds_path = Path.home() / ".cloudflared" / f"{tunnel_id}.json"
        if not creds_path.exists():
            raise RuntimeError(f"Credentials file not found at {creds_path}")

        creds = json.loads(creds_path.read_text(encoding="utf-8"))
        account_id = creds["AccountTag"]
        tunnel_secret_b64 = creds["TunnelSecret"]

        # Step 3: Route DNS (non-fatal if record already exists)
        dns_res = subprocess.run(
            [cloudflared_path, "tunnel", "route", "dns", "--overwrite-dns", tunnel_name, hostname],
            capture_output=True,
            text=True,
        )
        if dns_res.returncode != 0:
            dns_output = f"{dns_res.stdout}\n{dns_res.stderr}"
            if "already exists" in dns_output.lower():
                logger.info(f"DNS record for '{hostname}' already exists, continuing.")
            else:
                raise RuntimeError(f"Failed to route DNS for '{hostname}': {dns_output}")

        # Step 4: Build the tunnel token
        token_data = {
            "a": account_id,
            "t": tunnel_id,
            "s": tunnel_secret_b64
        }
        tunnel_token = base64.b64encode(json.dumps(token_data).encode("utf-8")).decode("utf-8")

        return tunnel_token, hostname

