from __future__ import annotations

import shutil
import typing
from pathlib import Path

from pilot.config.logs import LogsConfig
from pilot.exceptions import BenchError
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.internal.template import Template
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import is_linux
from pilot.managers.systemd_user import (
    SystemdUserMixin,
    install_user_service,
    user_service_installed,
)
from pilot.utils import cli_root, run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

TEMPLATES_DIR = Path(__file__).parent / "templates"

_CONF_TEMPLATE = Template.from_path(TEMPLATES_DIR / "fluent-bit.conf.template")
_PARSERS_CONF = (TEMPLATES_DIR / "parsers.conf").read_text()
_PILOT_LUA = (TEMPLATES_DIR / "pilot.lua").read_text()

# A long-running user service: Fluent Bit tails every bench under the CLI root
# as the bench user. The token lives in an EnvironmentFile written separately
# so rotation never touches the config.
UNIT_TEMPLATE = """\
[Unit]
Description=Fluent Bit log shipper for Pilot

[Service]
Type=simple
WorkingDirectory={cli_root}
EnvironmentFile={conf_dir}/token.env
ExecStart={fluent_bit} -c {conf_dir}/fluent-bit.conf
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
"""


class LogsConfigurator(SystemdUserMixin):
    """Installs Fluent Bit as a systemd-user service shipping bench logs.

    Renders the shared fluent-bit.conf template, copies the parsers and Lua
    normalizer next to it, and enables a user service. Installing the binary
    (if missing) mirrors how the WAF installs the ModSecurity nginx module.
    """

    unit_name = "pilot-fluent-bit.service"

    def __init__(self, bench: "Bench | None" = None) -> None:
        self.bench = bench
        self.conf_dir = cli_root() / "system" / "fluent-bit"
        self.state_dir = self.conf_dir / "state"

    def setup(self) -> None:
        if not is_linux():
            raise BenchError("Log shipping is only supported on linux based machines.")
        if shutil.which("fluent-bit") is None:
            get_package_manager().install("fluent-bit")
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def install(self, config: LogsConfig) -> None:
        self._write_configs(config)
        if user_service_installed(self.unit_name):
            self._restart_service()
            return
        install_user_service(
            unit_dir=self.conf_dir,
            unit_name=self.unit_name,
            unit_text=self._render_unit(),
        )

    def remove(self) -> None:
        env = self._systemctl_env()
        run_command(self._systemctl("stop", self.unit_name), env=env)
        run_command(self._systemctl("disable", self.unit_name), env=env)
        for name in ("fluent-bit.conf", "parsers.conf", "pilot.lua", "token.env", self.unit_name):
            target = self.conf_dir / name
            if target.exists():
                target.unlink()

    def _write_configs(self, config: LogsConfig) -> None:
        self.conf_dir.mkdir(parents=True, exist_ok=True)
        self._write_conf(config)
        (self.conf_dir / "parsers.conf").write_text(_PARSERS_CONF)
        (self.conf_dir / "pilot.lua").write_text(_PILOT_LUA)
        self._write_token(config.token)

    def _write_conf(self, config: LogsConfig) -> None:
        rendered = _CONF_TEMPLATE.render(
            cli_root=cli_root(),
            conf_dir=self.conf_dir,
            host=self._host(config.endpoint),
            port=self._port(config.endpoint),
            uri=self._uri(config.endpoint),
            tls=config.endpoint.startswith("https://"),
        )
        (self.conf_dir / "fluent-bit.conf").write_text(rendered)

    def _write_token(self, token: str) -> None:
        atomic_write_private_text(self.conf_dir / "token.env", f"DATUM_LOG_TOKEN={token}")

    def _render_unit(self) -> str:
        return UNIT_TEMPLATE.format(
            cli_root=cli_root(),
            fluent_bit=self._fluent_bit_binary,
            conf_dir=self.conf_dir,
        )

    @property
    def _fluent_bit_binary(self) -> str:
        if found := shutil.which("fluent-bit"):
            return found
        default_path = Path("/opt/fluent-bit/bin/fluent-bit")
        if default_path.exists():
            return str(default_path)
        raise BenchError("fluent-bit binary not found. Install it before setting up log shipping.")

    def _restart_service(self) -> None:
        env = self._systemctl_env()
        run_command(self._systemctl("restart", self.unit_name), env=env)

    @staticmethod
    def _host(endpoint: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        return parsed.hostname or ""

    @staticmethod
    def _port(endpoint: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        return str(parsed.port or (443 if parsed.scheme == "https" else 80))

    @staticmethod
    def _uri(endpoint: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        return parsed.path or "/v1/logs/ingest"
