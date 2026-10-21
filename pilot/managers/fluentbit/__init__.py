from __future__ import annotations

import shutil
import subprocess
import typing
from pathlib import Path

from pilot.config.telemetry import TelemetryConfig
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
# The telemetry endpoint is a base URL; each shipper appends its own route, as the metrics
# client does. A path in the configured endpoint is not honoured, so the two cannot drift.
LOGS_PATH = "/v1/logs/ingest"

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
# Fluent Bit logs every refused request. Cap what reaches the journal, and
# through it syslog, so a refusing endpoint cannot fill the disk.
LogRateLimitIntervalSec=30s
LogRateLimitBurst=200

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
        if self.binary_path is None:
            get_package_manager().install("fluent-bit")
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def sync(self, config: TelemetryConfig) -> None:
        """Ship logs whenever [telemetry] has a credential, as metrics do on each monitor tick.

        Fluent Bit reads its token only at start, so a changed config or token restarts it.
        """
        if not config.is_shipping_logs or self.binary_path is None:
            # In case it's still running remove it the config is gone.
            if self.is_running:
                self.remove()
            return
        if self.is_running and self._read_files() == self._render_files(config):
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.install(config)

    @property
    def is_running(self) -> bool:
        result = subprocess.run(
            self._systemctl("is-active", "--quiet", self.unit_name), env=self._systemctl_env()
        )
        return result.returncode == 0

    def install(self, config: TelemetryConfig) -> None:
        """Write the config and unit. A running shipper is restarted, as `enable --now` would not."""
        self._write_configs(config)
        was_installed = user_service_installed(self.unit_name)
        install_user_service(
            unit_dir=self.conf_dir,
            unit_name=self.unit_name,
            unit_text=self._render_unit(),
        )
        if was_installed:
            self._restart_service()

    def remove(self) -> None:
        env = self._systemctl_env()
        run_command(self._systemctl("stop", self.unit_name), env=env)
        run_command(self._systemctl("disable", self.unit_name), env=env)
        for name in ("fluent-bit.conf", "parsers.conf", "pilot.lua", "token.env", self.unit_name):
            target = self.conf_dir / name
            if target.exists():
                target.unlink()

    def _write_configs(self, config: TelemetryConfig) -> None:
        self.conf_dir.mkdir(parents=True, exist_ok=True)
        files = self._render_files(config)
        atomic_write_private_text(self.conf_dir / "token.env", files.pop("token.env"))
        for name, text in files.items():
            (self.conf_dir / name).write_text(text)

    def _render_files(self, config: TelemetryConfig) -> dict[str, str]:
        conf = _CONF_TEMPLATE.render(
            cli_root=cli_root(),
            conf_dir=self.conf_dir,
            host=self._host(config.endpoint),
            port=self._port(config.endpoint),
            uri=LOGS_PATH,
            tls=config.endpoint.startswith("https://"),
        )
        return {
            "fluent-bit.conf": conf,
            "parsers.conf": _PARSERS_CONF,
            "pilot.lua": _PILOT_LUA,
            "token.env": f"DATUM_LOG_TOKEN={config.token}",
            self.unit_name: self._render_unit(),
        }

    def _read_files(self) -> dict[str, str]:
        names = ("fluent-bit.conf", "parsers.conf", "pilot.lua", "token.env", self.unit_name)
        return {name: path.read_text() for name in names if (path := self.conf_dir / name).exists()}

    def _render_unit(self) -> str:
        if not (binary := self.binary_path):
            raise BenchError("fluent-bit binary not found. Install it before setting up log shipping.")
        return UNIT_TEMPLATE.format(cli_root=cli_root(), fluent_bit=binary, conf_dir=self.conf_dir)

    @property
    def binary_path(self) -> str | None:
        """fluent-bit on PATH, else where the official package installs it."""
        if found := shutil.which("fluent-bit"):
            return found
        default_path = Path("/opt/fluent-bit/bin/fluent-bit")
        return str(default_path) if default_path.exists() else None

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
